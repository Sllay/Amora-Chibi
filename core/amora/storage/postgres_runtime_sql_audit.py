"""Phase 2F.1: read-only audit of Core SQL call sites for a PostgreSQL cutover.

This scanner never connects to any database, reads secrets, or modifies files.
It discovers explicit and unresolved SQL call sites in the *serving* Core.
It is deliberately conservative: "portable_candidate" means only that no
known SQLite marker was detected, NOT that a query works on PostgreSQL.

Usage:
    python -m amora.storage.postgres_runtime_sql_audit
    python -m amora.storage.postgres_runtime_sql_audit --repo-root .

This module does not implement SQL translation. Any attempt to certify the
PostgreSQL serving Core without a complete reviewed mapping must fail closed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# These are *known syntax/semantic hazards*, not a complete SQL parser.
# Even a SQL statement with no flagged features remains UNREVIEWED.
_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sqlite_pragma", re.compile(r"\bPRAGMA\b", re.I)),
    ("sqlite_catalog", re.compile(r"\b(?:sqlite_schema|sqlite_master|sqlite_sequence)\b", re.I)),
    ("fts5_or_bm25", re.compile(r"\b(?:FTS5|FTS5VOCAB|BM25|MEMORY_FTS)\b|\bMATCH\s+['\"?:]", re.I)),
    ("sqlite_json1", re.compile(r"\bJSON_(?:EXTRACT|EACH|ARRAY_LENGTH|VALID|SET|INSERT|REMOVE|TYPE|OBJECT)\s*\(", re.I)),
    ("sqlite_conflict", re.compile(r"\b(?:INSERT\s+OR\s+(?:IGNORE|REPLACE|ABORT|ROLLBACK|FAIL)|REPLACE\s+INTO)\b", re.I)),
    ("sqlite_implicit_tx", re.compile(r"\bBEGIN\s+IMMEDIATE\b", re.I)),
    ("sqlite_functions", re.compile(r"\b(?:LAST_INSERT_ROWID|CHANGES|TOTAL_CHANGES)\s*\(", re.I)),
    ("qmark_placeholder", re.compile(r"(?<!\?)\?(?!\?)")),
)
_STARTS_SQL = re.compile(
    r"^\s*(?:SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|"
    r"PRAGMA|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE|EXPLAIN|VACUUM|ATTACH|"
    r"DETACH|REINDEX)\b",
    re.I,
)
_CALL_METHODS = frozenset({"execute", "executemany", "executescript"})
_HELPER_METHODS = frozenset({"execute_atomic_statement"})
_EXCLUDE_PARTS = frozenset({"__pycache__", ".venv", "venv", "site-packages"})


@dataclass(frozen=True)
class QuerySite:
    path: str
    line: int
    method: str
    classification: str
    flags: tuple[str, ...]
    fingerprint: str | None


def _constants(tree: ast.AST) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        values[target.id] = node.value.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                values[node.target.id] = node.value.value
    return values


def _static_sql(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        lhs = _static_sql(node.left, constants)
        rhs = _static_sql(node.right, constants)
        if lhs is not None and rhs is not None:
            return lhs + rhs
    return None


def scan_source(source: str, *, path: str) -> list[QuerySite]:
    """Classify static SQL and explicitly mark unresolved/dynamic call sites.

    AST parse failures are not silently discarded; an invalid Core file
    causes CI to fail rather than claiming complete coverage.
    """
    tree = ast.parse(source, filename=path)
    constants = _constants(tree)
    result: list[QuerySite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
        elif isinstance(node.func, ast.Name):
            method = node.func.id
        else:
            continue
        if method in _CALL_METHODS:
            sql_index = 0
        elif method in _HELPER_METHODS:
            sql_index = 2
        else:
            continue
        if len(node.args) <= sql_index:
            # Indirect keyword/dynamic SQL is not certified.
            result.append(QuerySite(path, node.lineno, method, "dynamic", ("unresolved_sql",), None))
            continue
        sql = _static_sql(node.args[sql_index], constants)
        if sql is None:
            result.append(QuerySite(path, node.lineno, method, "dynamic", ("unresolved_sql",), None))
            continue
        if not _STARTS_SQL.search(sql):
            # A non-SQL execute() on some other object is not a DB query.
            continue
        flags = tuple(name for name, pattern in _MARKERS if pattern.search(sql))
        normalized = " ".join(sql.split())
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        result.append(QuerySite(
            path, node.lineno, method,
            "sqlite_sensitive" if flags else "unreviewed_sql",
            flags, fingerprint,
        ))
    return sorted(result, key=lambda row: (row.path, row.line, row.method))


def scan_repository(repo_root: Path) -> list[QuerySite]:
    """Scan all serving Core modules; exclude all PostgreSQL experiments."""
    root = Path(repo_root) / "core" / "amora"
    if not root.is_dir():
        raise ValueError("AMORA_CORE_SOURCE_MISSING")
    results: list[QuerySite] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in _EXCLUDE_PARTS for part in path.parts):
            continue
        if path.name.startswith("postgres_"):
            # Exclude lab/experimental conversion SQL, not serving Core SQL.
            continue
        relative = path.relative_to(repo_root).as_posix()
        results.extend(scan_source(path.read_text(encoding="utf-8"), path=relative))
    if not results:
        raise ValueError("AMORA_SQL_AUDIT_EMPTY")
    return results


def report(repo_root: Path) -> dict[str, Any]:
    """Return structural metadata, never SQL text or query parameter values."""
    sites = scan_repository(repo_root)
    counts = Counter(site.classification for site in sites)
    features = Counter(flag for site in sites for flag in site.flags)
    by_file = Counter(site.path for site in sites)
    return {
        "status": "incomplete",
        "phase": "aiven-phase2f1-runtime-sql-inventory",
        "serving_postgres_certified": False,
        "production_backend_switched": False,
        "sql_call_sites": len(sites),
        "classifications": dict(sorted(counts.items())),
        "feature_counts": dict(sorted(features.items())),
        "source_file_counts": dict(sorted(by_file.items())),
        "sites": [asdict(site) for site in sites],
    }


def require_postgres_serving_ready(_report: dict[str, Any] | None = None) -> None:
    """Always blocks production selection until a separate reviewed release."""
    raise RuntimeError("CORE_POSTGRES_SQL_DIALECT_NOT_CERTIFIED")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Amora SQL dialect inventory")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    print("AMORA_PG_RUNTIME_SQL_AUDIT " + json.dumps(
        report(args.repo_root), ensure_ascii=True, sort_keys=True,
        separators=(",", ":"),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
