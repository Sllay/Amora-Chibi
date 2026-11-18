"""Prepare a disposable PostgreSQL 18 Core fixture, never production storage.

The setup has no credentials, no DSN, no file path, no Aiven/Turso connection
and accepts only a caller-supplied local PostgreSQL 18 connection to
amora_pg_synthetic. This is an explicit development bootstrap, not the
serving Core migration path or a private PCS4C importer.
"""
from __future__ import annotations

from contextlib import closing

from .postgres_memory_lab import ensure_local_lab
from .postgres_v18_manifest import synthetic_v18_connection
from .postgres_v18_lab_schema import install_pg_v18_lab
from .postgres_synthetic_import import import_synthetic_v18
from .postgres_fts_bm25_lab import install_bm25_lab, import_synthetic_fts5_vocabulary
from .postgres_voice_stop_lab import install_stop_table
from .postgres_episode_lab_schema import install_episode_queue_lab
from .postgres_memory_epoch_lab import install_pg_memory_epoch_lab


def prepare_disposable_core_v18(raw, *, include_episode_candidate: bool = False,
                               include_memory_epoch_candidate: bool = False) -> dict[str, object]:
    """Import the exact canonical *invented* v18 fixture before PG add-ons.

    Order is intentional: the importer demands precisely the 49 original
    relational tables plus memory_search, so FTS posting and stop tables
    must be created only after that verified import completes.
    """
    ensure_local_lab(raw)
    if getattr(raw, "closed", True) or getattr(raw, "autocommit", False) is not True:
        raise ValueError("PG_SYNTHETIC_AUTOCOMMIT_REQUIRED")
    with closing(synthetic_v18_connection()) as source:
        structure = install_pg_v18_lab(raw)
        copied = import_synthetic_v18(source, raw)
        install_bm25_lab(raw)
        postings = import_synthetic_fts5_vocabulary(source, raw)
        install_stop_table(raw)
        if include_episode_candidate:
            install_episode_queue_lab(raw)
        if include_memory_epoch_candidate:
            install_pg_memory_epoch_lab(raw)
    return {
        "mode": "disposable-local-only",
        "structure": structure,
        "import": copied,
        "postings": postings,
        "voice_stop_installed": True,
        "sensory_episode_candidate_installed": include_episode_candidate,
        "memory_epoch_candidate_installed": include_memory_epoch_candidate,
        "schema19_migration_performed": False,
        "production_backend_switched": False,
    }
