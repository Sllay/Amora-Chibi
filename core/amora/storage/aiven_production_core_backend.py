"""Explicit production PG18 Core cutover, never staging by accident.

No automatic migration, secret echo, provider fallback or DB target discovery.
The operator must separately enable the selector *and* authorize the verified
Aiven PG18 cutover on an existing service. Deployment remains a human action.
"""
from __future__ import annotations

import os

from .aiven_staging_core_backend import _tls_pinned_aiven_backend
from .postgres_runtime_connection import (
    PostgresBackendCandidate, PostgresRuntimeError,
)

_PRODUCTION_APPROVAL = "AUTHORIZE_VERIFIED_AIVEN_PG18_CORE_CUTOVER"


def production_core_backend_from_environment() -> PostgresBackendCandidate:
    """Select the reviewed PG18 target only after independent production gate.

    The private snapshot is already imported, a native PG18 offsite backup
    restored independently and verified, and new-memory read/write tested.
    This function is fail-closed and never performs migrations/import/cutover.
    """
    if os.getenv("AMORA_STORAGE_BACKEND", "") != "postgres":
        raise PostgresRuntimeError("PG_PRODUCTION_SELECTOR_REQUIRED")
    if os.getenv("AMORA_PG_PRODUCTION_APPROVAL", "") != _PRODUCTION_APPROVAL:
        raise PostgresRuntimeError("PG_PRODUCTION_CUTOVER_APPROVAL_REQUIRED")
    if os.getenv("AMORA_MODE", "").strip().lower() != "cloud":
        raise PostgresRuntimeError("PG_PRODUCTION_CLOUD_MODE_REQUIRED")
    if os.getenv("AMORA_PG_STAGING_APPROVAL"):
        raise PostgresRuntimeError("PG_PRODUCTION_STAGING_APPROVAL_CONFLICT")
    if (
        os.getenv("AMORA_DURABLE_FILESYSTEM", "").lower() != "true"
        and os.getenv("AMORA_ASSET_STORAGE", "") != "database"
    ):
        raise PostgresRuntimeError("PG_PRODUCTION_AVATAR_DURABILITY_REQUIRED")
    return _tls_pinned_aiven_backend("amora-pg18-production-guarded")
