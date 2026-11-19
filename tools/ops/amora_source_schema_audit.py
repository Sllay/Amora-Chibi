"""Private artifact schema comparison, read-only, aggregate diagnostics only."""
from __future__ import annotations
from contextlib import closing
import json
import os
from pathlib import Path
import re
import tempfile

from amora_pg18_restore_rehearsal import _extract
from amora.storage.pcs4_recovery import _open, _schema, _tables, verify_snapshot
from amora.storage.postgres_v18_manifest import synthetic_v18_connection
from amora.storage.postgres_run3_snapshot_preflight import inspect_private_snapshot, Run3SnapshotError

def run() -> dict:
    archive = Path(os.environ["RUNNER_TEMP"]) / "amora-rehearsal-artifact" / "amora-backup.zip"
    with tempfile.TemporaryDirectory(prefix="amora-source-check-") as temp:
        os.chmod(temp, 0o700)
        path = _extract(archive, Path(temp))
        basic = verify_snapshot(path, expected_schema=18)
        with closing(_open(path)) as source, closing(synthetic_v18_connection()) as reference:
            a, b = _schema(source), _schema(reference)
            shared = set(a) & set(b)
            expected_only = len(set(b) - set(a))
            source_only = len(set(a) - set(b))
            exact_diffs = sum(a[key] != b[key] for key in shared)
            def normalize(sql):
                return " ".join(sql.split()).strip().casefold() if sql else ""
            normalized_diffs = sum(normalize(a[key]) != normalize(b[key]) for key in shared)
            table_parity = _tables(source) == _tables(reference)
            mismatches = []
            for ordinal, key in enumerate(sorted(shared), 1):
                if a[key] == b[key]:
                    continue
                expected_kind = reference.execute(
                    "SELECT type FROM sqlite_schema WHERE name=?", (key,)
                ).fetchone()[0]
                # Object name and DDL remain private, even if the expected
                # object originates from a public static migration.
                normalized_a, normalized_b = normalize(a[key]), normalize(b[key])
                tokens = lambda sql: re.findall(
                    r"(?i)[a-z_][a-z_0-9]*|\d+|[^\s]", sql
                )
                ta, tb = tokens(normalized_a), tokens(normalized_b)
                import difflib
                edits = [
                    (tag, a1, a2, b1, b2)
                    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(
                        None, ta, tb, autojunk=False
                    ).get_opcodes() if tag != "equal"
                ]
                pragma_parity = "not_applicable"
                if expected_kind == "table":
                    checks = ("table_xinfo", "foreign_key_list", "index_list")
                    pragma_parity = "equal" if all(
                        source.execute(f'PRAGMA {p}("{key}")').fetchall()
                        == reference.execute(f'PRAGMA {p}("{key}")').fetchall()
                        for p in checks
                    ) else "different"
                elif expected_kind == "index":
                    pragma_parity = "equal" if (
                        source.execute(f'PRAGMA index_xinfo("{key}")').fetchall()
                        == reference.execute(f'PRAGMA index_xinfo("{key}")').fetchall()
                    ) else "different"
                mismatches.append({
                    "canonical_ordinal": ordinal,
                    # Public canonical fixture name only; never print a value
                    # found only in the private source.
                    "canonical_fixture_object": key,
                    "expected_static_sql_insertions": [
                        " ".join(tb[b1:b2])[:140]
                        for tag, a1, a2, b1, b2 in edits if tag == "insert"
                    ][:5],
                    "check_keyword_count_equal": (
                        len(re.findall(r"\bCHECK\b", normalized_a, re.I))
                        == len(re.findall(r"\bCHECK\b", normalized_b, re.I))
                    ),
                    "unique_keyword_count_equal": (
                        len(re.findall(r"\bUNIQUE\b", normalized_a, re.I))
                        == len(re.findall(r"\bUNIQUE\b", normalized_b, re.I))
                    ),
                    "object_kind": expected_kind if expected_kind in ("table","index","trigger","view") else "other",
                    "normalized_sql_equal": normalized_a == normalized_b,
                    "lexical_edit_count": len(edits),
                    "edit_shapes": [e[0] for e in edits][:8],
                    "pragma_metadata": pragma_parity,
                })
        strict_reason = "ok"
        try:
            inspect_private_snapshot(path)
        except Run3SnapshotError as exc:
            candidate = str(exc)
            strict_reason = candidate if re.fullmatch(r"RUN3_[A-Z0-9_]{8,100}", candidate) else "RUN3_BLOCKED"
        return {
            "status": "schema_parity_diagnosed",
            "pcs4c_integrity": "ok",
            "schema_version": basic["schema_version"],
            "logical_tables": basic["tables"],
            "logical_rows": basic["rows"],
            "asset_count": basic["assets"],
            "canonical_table_set_equal": table_parity,
            "ddl_missing_objects_count": expected_only,
            "ddl_extra_objects_count": source_only,
            "ddl_exact_differences_count": exact_diffs,
            "ddl_normalized_differences_count": normalized_diffs,
            "ddl_mismatch_classification": mismatches,
            "strict_gate": strict_reason,
            "aiven_modified": False,
            "cutover_authorized": False,
        }

if __name__ == "__main__":
    try:
        print("AMORA_SOURCE_SCHEMA_AUDIT " + json.dumps(run(), sort_keys=True), flush=True)
    except Exception:
        print("AMORA_SOURCE_SCHEMA_AUDIT " + json.dumps({
            "status": "blocked",
            "reason": "SOURCE_READONLY_DIAGNOSTIC_FAILED",
            "aiven_modified": False,
            "cutover_authorized": False,
        }, sort_keys=True), flush=True)
        raise SystemExit(2)
