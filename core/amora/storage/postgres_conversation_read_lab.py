"""Phase 2F.2: read-only bridge into ORIGINAL ConversationStore methods.

This is a strictly isolated integration harness: it binds a caller-supplied
disposable PostgreSQL 18 loopback connection to exactly eight reviewed
SQLite v18 ConversationStore SELECT statements. Each statement has an
explicit PostgreSQL equivalent; no general qmark substitution or SQL parser.

The real ConversationStore Python methods execute unchanged on this
lab adapter. All writes, transactions, DDL and unknown queries fail closed.
This is not a production backend and cannot be selected via environment.
WARNING: canonical list/count/get methods lack a per-principal ACL. They are
safe to compare only with synthetic records in the disposable local DB.
Never expose this wrapper as a serving conversation/Discord API.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .postgres_memory_lab import ensure_local_lab
from .postgres_sql_connection_lab import LabResult

_MAX_PARAM_LENGTH = 256


class ConversationReadLabError(ValueError):
    pass


@dataclass(frozen=True)
class _ReviewedQuery:
    pg_sql: str
    param_types: tuple[type, ...]
    max_rows: int


# The keys below MUST match canonical ConversationStore SQL text exactly,
# after whitespace normalization. No SELECT * or schema search_path fallback.
_QUERIES = {
    "SELECT result_json FROM conversation_turn_decisions WHERE dedupe_key = ?":
        _ReviewedQuery(
            "SELECT result_json FROM amora_pg_v18_lab.conversation_turn_decisions "
            "WHERE dedupe_key=%s",
            (str,), 1,
        ),
    "SELECT result_json FROM conversation_turn_decisions ORDER BY created_at DESC LIMIT ?":
        _ReviewedQuery(
            "SELECT result_json FROM amora_pg_v18_lab.conversation_turn_decisions "
            "ORDER BY created_at DESC LIMIT %s",
            (int,), 200,
        ),
    "SELECT COUNT(*) FROM conversation_turn_decisions":
        _ReviewedQuery(
            "SELECT COUNT(*) FROM amora_pg_v18_lab.conversation_turn_decisions",
            (), 1,
        ),
    "SELECT utterance_json FROM speech_utterances WHERE dedupe_key = ?":
        _ReviewedQuery(
            "SELECT utterance_json FROM amora_pg_v18_lab.speech_utterances "
            "WHERE dedupe_key=%s",
            (str,), 1,
        ),
    "SELECT utterance_json FROM speech_utterances WHERE id = ?":
        _ReviewedQuery(
            "SELECT utterance_json FROM amora_pg_v18_lab.speech_utterances "
            "WHERE id=%s",
            (str,), 1,
        ),
    "SELECT utterance_json FROM speech_utterances ORDER BY created_at DESC LIMIT ?":
        _ReviewedQuery(
            "SELECT utterance_json FROM amora_pg_v18_lab.speech_utterances "
            "ORDER BY created_at DESC LIMIT %s",
            (int,), 200,
        ),
    "SELECT COUNT(*) FROM speech_utterances WHERE status IN (?, ?)":
        _ReviewedQuery(
            "SELECT COUNT(*) FROM amora_pg_v18_lab.speech_utterances "
            "WHERE status IN (%s,%s)",
            (str, str), 1,
        ),
    "SELECT COUNT(*) FROM speech_utterances WHERE status = ?":
        _ReviewedQuery(
            "SELECT COUNT(*) FROM amora_pg_v18_lab.speech_utterances "
            "WHERE status=%s",
            (str,), 1,
        ),
}


def _normalize(sql: str) -> str:
    if not isinstance(sql, str):
        raise ConversationReadLabError("CONVERSATION_LAB_SQL_NOT_REVIEWED")
    return " ".join(sql.split())


_REVIEWED = {_normalize(key): value for key, value in _QUERIES.items()}
if len(_REVIEWED) != len(_QUERIES):
    raise RuntimeError("CONVERSATION_LAB_QUERY_COLLISION")


class PostgresConversationReadLab:
    """Minimal QueryResult-compatible read bridge for canonical methods."""

    def __init__(self, raw):
        ensure_local_lab(raw)
        if getattr(raw, "closed", True) or getattr(raw, "autocommit", False) is not True:
            raise ConversationReadLabError("CONVERSATION_LAB_AUTOCOMMIT_REQUIRED")
        self._raw = raw
        self._closed = False

    @property
    def in_transaction(self) -> bool:
        return False

    def execute(self, sql: str, parameters: Iterable[object] = ()) -> LabResult:
        if self._closed:
            raise ConversationReadLabError("CONVERSATION_LAB_CLOSED")
        ensure_local_lab(self._raw)
        query = _REVIEWED.get(_normalize(sql))
        if query is None:
            raise ConversationReadLabError("CONVERSATION_LAB_SQL_NOT_REVIEWED")
        if isinstance(parameters, (str, bytes, dict)):
            raise ConversationReadLabError("CONVERSATION_LAB_INVALID_PARAMETERS")
        try:
            values = tuple(parameters)
        except TypeError as exc:
            raise ConversationReadLabError("CONVERSATION_LAB_INVALID_PARAMETERS") from exc
        if len(values) != len(query.param_types):
            raise ConversationReadLabError("CONVERSATION_LAB_PARAMETER_COUNT")
        for value, expected in zip(values, query.param_types, strict=True):
            if type(value) is not expected:
                raise ConversationReadLabError("CONVERSATION_LAB_PARAMETER_TYPE")
            if expected is str and (not value or len(value) > _MAX_PARAM_LENGTH):
                raise ConversationReadLabError("CONVERSATION_LAB_PARAMETER_SIZE")
            if expected is int and not 1 <= value <= 200:
                raise ConversationReadLabError("CONVERSATION_LAB_LIMIT_INVALID")
        with self._raw.cursor() as cursor:
            cursor.execute(query.pg_sql, values)
            if cursor.description is None:
                raise ConversationReadLabError("CONVERSATION_LAB_READ_EXPECTED")
            columns = tuple(column.name for column in cursor.description)
            rows = cursor.fetchmany(query.max_rows + 1)
            if len(rows) > query.max_rows:
                raise ConversationReadLabError("CONVERSATION_LAB_RESULT_OVERFLOW")
            return LabResult(columns=columns, values=rows, rowcount=cursor.rowcount)

    def executemany(self, *_args, **_kwargs):
        raise ConversationReadLabError("CONVERSATION_LAB_READ_ONLY")

    def executescript(self, *_args, **_kwargs):
        raise ConversationReadLabError("CONVERSATION_LAB_READ_ONLY")

    def __enter__(self):
        raise ConversationReadLabError("CONVERSATION_LAB_READ_ONLY")

    def __exit__(self, *_args):
        raise ConversationReadLabError("CONVERSATION_LAB_READ_ONLY")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._raw.close()


def approved_query_count() -> int:
    return len(_REVIEWED)
