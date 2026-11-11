"""Canonical storage schema contract.

Migration modules describe individual historical steps. Runtime readiness,
recovery and tests should depend on this module instead of importing the newest
migration file or hardcoding a schema number in multiple places.
"""

LATEST_SCHEMA_VERSION = 18
MIN_AUDITED_REMOTE_IMPORT_SCHEMA_VERSION = 10
AUDITED_REMOTE_IMPORT_SCHEMA_VERSIONS = frozenset(
    range(MIN_AUDITED_REMOTE_IMPORT_SCHEMA_VERSION, LATEST_SCHEMA_VERSION + 1)
)
