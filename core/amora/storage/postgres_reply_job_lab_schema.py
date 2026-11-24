"""Install reviewed reply component only in the disposable PG18 lab."""
from .postgres_memory_lab import ensure_local_lab
from .postgres_runtime_connection import PostgresTargetPolicy
from .postgres_reply_job_migration import prepare_reply_schema, MIGRATION_APPROVAL


def install_reply_job_lab(raw):
    ensure_local_lab(raw)
    return prepare_reply_schema(raw, PostgresTargetPolicy(), approval=MIGRATION_APPROVAL)
