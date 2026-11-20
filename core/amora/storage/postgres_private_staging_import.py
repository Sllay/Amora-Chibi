"""Private PCS.4C v18 -> already-prepared empty PostgreSQL 18 staging import.

This module never reads credentials, selects a runtime backend, runs from
environment, creates a schema or authorizes cutover. The caller supplies an
explicit, already connected target plus pinned PostgresTargetPolicy. The
source must be a fully verified private snapshot/sidecar. Writes are confined
to one transaction in an EMPTY, previously certified staging schema.

Operator approval to import does not certify current source freshness,
backup/PITR, production readiness or permission to activate the Core.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
from itertools import zip_longest
from pathlib import Path
import sqlite3

from .pcs4_recovery import _open, _sha
from .postgres_run3_snapshot_preflight import inspect_private_snapshot
from .postgres_run3_preflight import inspect_prepared_postgres
from .postgres_runtime_connection import PostgresTargetPolicy
from .postgres_v18_lab_schema import _q, _ref, LAB_SCHEMA
from .postgres_v18_manifest import build_manifest

_BATCH = 100
_CONFIRM = "IMPORT_VERIFIED_V18_TO_EMPTY_ISOLATED_STAGING"
_LOCAL_HOSTS = frozenset(("127.0.0.1", "::1", "localhost"))


class PrivateStagingImportError(RuntimeError):
    """Fixed error codes; never include private content, DSN or paths."""


def _typed(row) -> tuple:
    # Psycopg may represent bytea as bytes or memoryview.
    return tuple(bytes(v) if isinstance(v, memoryview) else v for v in row)


def _row_digest(rows) -> Counter[bytes]:
    return Counter(
        hashlib.sha256(repr(_typed(row)).encode("utf-8")).digest()
        for row in rows
    )


def _copy_and_certify(source, target, source_table: str, target_table: str,
                      columns: tuple[str, ...]) -> int:
    fields = ", ".join(_q(x) for x in columns)
    placeholders = ", ".join("%s" for _ in columns)
    select = "SELECT " + fields + " FROM " + _q(source_table)
    insert = "INSERT INTO " + _ref(target_table) + " (" + fields + ") VALUES (" + placeholders + ")"
    rows = source.execute(select)
    count = 0
    with target.cursor() as cursor:
        while batch := rows.fetchmany(_BATCH):
            cursor.executemany(insert, batch)
            count += len(batch)
    source_check = _row_digest(source.execute(select))
    target_check = _row_digest(target.execute(
        "SELECT " + fields + " FROM " + _ref(target_table)
    ))
    if source_check != target_check:
        raise PrivateStagingImportError("PG_PRIVATE_TYPED_ROW_DRIFT")
    return count


def _import_vocab(source: sqlite3.Connection, target) -> tuple[int, int]:
    # Only the IN-MEMORY copy may receive a virtual helper table. The actual
    # snapshot and sidecar are opened read-only and are never changed.
    source.execute(
        "CREATE VIRTUAL TABLE amora_staging_vocab "
        "USING fts5vocab(memory_fts,'instance')"
    )
    docs = source.execute(
        "SELECT f.memory_id, COUNT(v.term) FROM memory_fts AS f "
        "LEFT JOIN amora_staging_vocab AS v ON v.doc=f.rowid "
        "GROUP BY f.memory_id ORDER BY f.memory_id"
    )
    postings = source.execute(
        "SELECT f.memory_id, v.term, COUNT(*) "
        "FROM amora_staging_vocab AS v JOIN memory_fts AS f "
        "ON f.rowid=v.doc GROUP BY f.memory_id,v.term "
        "ORDER BY f.memory_id,v.term"
    )
    ndoc = 0
    npost = 0
    with target.cursor() as cursor:
        while batch := docs.fetchmany(_BATCH):
            cursor.executemany(
                "INSERT INTO amora_pg_v18_lab.fts_doc_tokens "
                "(memory_id,document_length) VALUES (%s,%s)", batch
            )
            ndoc += len(batch)
        while batch := postings.fetchmany(_BATCH):
            cursor.executemany(
                "INSERT INTO amora_pg_v18_lab.fts_term_postings "
                "(memory_id,term,frequency) VALUES (%s,%s,%s)", batch
            )
            npost += len(batch)
    if (ndoc != source.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
            or target.execute(
                "SELECT COUNT(*) FROM amora_pg_v18_lab.fts_doc_tokens"
            ).fetchone()[0] != ndoc):
        raise PrivateStagingImportError("PG_PRIVATE_FTS_DOC_DRIFT")
    doc_len = target.execute(
        "SELECT COALESCE(SUM(document_length),0) "
        "FROM amora_pg_v18_lab.fts_doc_tokens"
    ).fetchone()[0]
    tf_sum = target.execute(
        "SELECT COALESCE(SUM(frequency),0) "
        "FROM amora_pg_v18_lab.fts_term_postings"
    ).fetchone()[0]
    if doc_len != tf_sum:
        raise PrivateStagingImportError("PG_PRIVATE_FTS_TERM_DRIFT")
    return ndoc, npost


def _ensure_empty(raw, table_names: list[str]) -> None:
    for name in table_names:
        if raw.execute("SELECT 1 FROM " + _ref(name) + " LIMIT 1").fetchone() is not None:
            raise PrivateStagingImportError("PG_PRIVATE_DESTINATION_NOT_EMPTY")


def _sequences(source: sqlite3.Connection, raw, tables: list[dict]) -> int:
    source_highwater = dict(source.execute("SELECT name,seq FROM sqlite_sequence"))
    adjusted = 0
    for item in tables:
        if "AUTOINCREMENT" not in item["sqlite_create_sql"].upper():
            continue
        pks = [col for col in item["columns"] if col["pk_ordinal"]]
        if len(pks) != 1:
            raise PrivateStagingImportError("PG_PRIVATE_IDENTITY_DRIFT")
        col = pks[0]["name"]
        max_row = raw.execute(
            "SELECT COALESCE(MAX(" + _q(col) + "),0) FROM " + _ref(item["name"])
        ).fetchone()[0]
        latest = max(max_row, source_highwater.get(item["name"], 0))
        if not latest:
            continue
        sequence = raw.execute(
            "SELECT pg_get_serial_sequence(%s,%s)",
            (LAB_SCHEMA + "." + item["name"], col),
        ).fetchone()[0]
        if not sequence:
            raise PrivateStagingImportError("PG_PRIVATE_IDENTITY_MISSING")
        # PostgreSQL sequence changes are non-transactional: an isolated
        # target must be discarded if a later import step fails.
        raw.execute("SELECT setval(%s::regclass,%s,true)", (sequence, latest))
        adjusted += 1
    return adjusted


def import_private_staging_v18(
    snapshot: str | Path, raw, target: PostgresTargetPolicy,
    *, confirmation: str = "",
) -> dict[str, object]:
    """Copy an operator-verified private backup to empty prepared PG staging.

    Fails closed BEFORE any write unless all structural, TLS, empty-target
    and operator-confirmation gates pass. Never performs schema creation.
    """
    if confirmation != _CONFIRM:
        raise PrivateStagingImportError("PG_PRIVATE_OPERATOR_CONFIRMATION_REQUIRED")
    if not isinstance(target, PostgresTargetPolicy) or target.schema != LAB_SCHEMA:
        raise PrivateStagingImportError("PG_PRIVATE_TARGET_POLICY_MISMATCH")
    # A remote target must have an independently verified TLS connection.
    if target.host not in _LOCAL_HOSTS and not target.require_tls:
        raise PrivateStagingImportError("PG_PRIVATE_REMOTE_TLS_REQUIRED")
    try:
        target.verify(raw)
        if getattr(raw, "autocommit", None) is not True:
            raise PrivateStagingImportError("PG_PRIVATE_AUTOCOMMIT_REQUIRED")
    except PrivateStagingImportError:
        raise
    except Exception:
        raise PrivateStagingImportError("PG_PRIVATE_TARGET_NOT_VERIFIED") from None

    # Verify the pair and byte-level source immutability outside PostgreSQL.
    try:
        source_report = inspect_private_snapshot(snapshot)
    except Exception:
        raise PrivateStagingImportError("PG_PRIVATE_SOURCE_NOT_VERIFIED") from None
    path = Path(snapshot)
    sidecar = Path(str(path) + ".avatar.zip")
    try:
        start = (_sha(path), _sha(sidecar))
    except Exception:
        raise PrivateStagingImportError("PG_PRIVATE_SOURCE_CHANGED") from None

    preflight = inspect_prepared_postgres(
        raw, target, require_memory_epoch=True,
        allow_empty_migrations_for_import=True,
    )
    # A full runtime structural_pass is insufficient: staging import
    # requires the strict, separately named EMPTY migrations state.
    if preflight.status == "structural_pass":
        # The migration ledger is already populated: never treat an old
        # import as fresh, even if the caller requests a second run.
        raise PrivateStagingImportError("PG_PRIVATE_DESTINATION_NOT_EMPTY")
    if preflight.status != "preimport_structural_pass":
        raise PrivateStagingImportError("PG_PRIVATE_TARGET_STRUCTURE_BLOCKED")

    manifest = build_manifest()
    ordinary = [t for t in manifest["tables"] if t["kind"] == "ordinary"]
    data_tables = [t["name"] for t in ordinary] + [
        "memory_search", "fts_doc_tokens", "fts_term_postings", "speech_turn_stops",
    ]
    try:
        _ensure_empty(raw, data_tables)
    except PrivateStagingImportError:
        raise
    except Exception:
        raise PrivateStagingImportError("PG_PRIVATE_TARGET_EMPTY_CHECK_FAILED") from None

    try:
        snapshot_source = _open(path)
        source_copy = sqlite3.connect(":memory:")
    except Exception:
        raise PrivateStagingImportError("PG_PRIVATE_SOURCE_COPY_FAILED") from None
    with closing(snapshot_source) as snapshot_source, closing(source_copy) as source:
        # A private, immutable memory copy allows fts5vocab construction without
        # writing any helper objects into the original PCS.4C backup.
        try:
            snapshot_source.backup(source)
            if (_sha(path), _sha(sidecar)) != start:
                raise PrivateStagingImportError("PG_PRIVATE_SOURCE_CHANGED")
        except PrivateStagingImportError:
            raise
        except Exception:
            raise PrivateStagingImportError("PG_PRIVATE_SOURCE_COPY_FAILED") from None
        total = 0
        documents = 0
        postings = 0
        sequences = 0
        try:
            with raw.transaction():
                raw.execute("SET CONSTRAINTS ALL DEFERRED")
                for table in ordinary:
                    columns = tuple(c["name"] for c in table["columns"])
                    total += _copy_and_certify(
                        source, raw, table["name"], table["name"], columns,
                    )
                documents = _copy_and_certify(
                    source, raw, "memory_fts", "memory_search",
                    ("memory_id", "content", "tags"),
                )
                vocab_docs, postings = _import_vocab(source, raw)
                if vocab_docs != documents:
                    raise PrivateStagingImportError("PG_PRIVATE_FTS_COUNT_DRIFT")
                for table in ordinary:
                    if raw.execute(
                        "SELECT COUNT(*) FROM " + _ref(table["name"])
                    ).fetchone()[0] != source.execute(
                        "SELECT COUNT(*) FROM " + _q(table["name"])
                    ).fetchone()[0]:
                        raise PrivateStagingImportError("PG_PRIVATE_TABLE_COUNT_DRIFT")
                if (_sha(path), _sha(sidecar)) != start:
                    raise PrivateStagingImportError("PG_PRIVATE_SOURCE_CHANGED")
                sequences = _sequences(source, raw, ordinary)
        except PrivateStagingImportError:
            raise
        except Exception:
            # The caller must discard a possibly partially modified isolated
            # target after any uncertain DB/commit failure; never retry blindly.
            raise PrivateStagingImportError("PG_PRIVATE_IMPORT_FAILED_REVIEW_TARGET") from None

    return {
        "status": "staged_import_completed",
        "schema_version": source_report["schema_version"],
        "relational_tables": len(ordinary),
        "relational_rows": total,
        "search_documents": documents,
        "search_postings": postings,
        "identity_sequences_reconciled": sequences,
        "typed_row_parity": True,
        "source_freshness": "not_certified",
        "external_backup_and_restore": "not_certified",
        "staging_only": True,
        "production_cutover_authorized": False,
    }
