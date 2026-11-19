"""Strict whitespace-only exception for two immutable v18 schema objects."""
from __future__ import annotations
from contextlib import closing
import sqlite3

from amora.storage.postgres_run3_snapshot_preflight import (
    _ddl_tokens, _v18_schema_equivalent,
)


def test_schema_migrations_whitespace_and_case_only_is_allowed():
    with closing(sqlite3.connect(":memory:")) as current, closing(sqlite3.connect(":memory:")) as canonical:
        current.execute("create table schema_migrations (version integer primary key, applied_at text)")
        canonical.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT)")
        assert _v18_schema_equivalent(current, canonical) is True


def test_schema_migrations_missing_check_constraint_is_rejected():
    with closing(sqlite3.connect(":memory:")) as current, closing(sqlite3.connect(":memory:")) as canonical:
        current.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)")
        canonical.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT CHECK (length(applied_at)>1))")
        assert _v18_schema_equivalent(current, canonical) is False


def test_schema_migrations_literal_whitespace_must_remain_exact():
    assert _ddl_tokens("DEFAULT 'a b'") != _ddl_tokens("DEFAULT 'ab'")
    assert _ddl_tokens("DEFAULT 'a b'") != _ddl_tokens("DEFAULT 'A B'")


def test_other_tables_remain_exact_only():
    with closing(sqlite3.connect(":memory:")) as current, closing(sqlite3.connect(":memory:")) as canonical:
        current.execute("create table human_memory (id integer primary key)")
        canonical.execute("CREATE TABLE human_memory(id INTEGER PRIMARY KEY)")
        assert _v18_schema_equivalent(current, canonical) is False


def test_sql_comments_and_unbalanced_quotes_fail_closed():
    assert _ddl_tokens("CREATE TABLE schema_migrations(id INT)-- comment") is None
    assert _ddl_tokens("DEFAULT 'unterminated") is None
