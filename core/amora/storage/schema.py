"""Canonical storage schema and explicitly gated runtime upgrade policy.

The existing serving Core and PCS.4C recovery stay on v18 by default.
Runtime v19 is available only after an operator separately opts in to the
certified migration. Merely deploying code must not migrate production.
"""

import os

LATEST_SCHEMA_VERSION = 18
SCHEMA19_RUNTIME_VERSION = 19
_SCHEMA19_CONFIRMATION = "CERTIFIED_V19_BACKUP_AND_ROLLBACK"
MIN_AUDITED_REMOTE_IMPORT_SCHEMA_VERSION = 10
AUDITED_REMOTE_IMPORT_SCHEMA_VERSIONS = frozenset(
    range(MIN_AUDITED_REMOTE_IMPORT_SCHEMA_VERSION, LATEST_SCHEMA_VERSION + 1)
)


def runtime_schema_target() -> int:
    """Resolve once during database bootstrap; reject incomplete opt-in."""
    selected = os.getenv("AMORA_RUNTIME_SCHEMA_TARGET", "").strip()
    confirmation = os.getenv("AMORA_RUNTIME_SCHEMA_CONFIRMATION", "").strip()
    if selected in {"", "18"}:
        if confirmation:
            raise ValueError("SCHEMA19_RUNTIME_CONFIRMATION_WITHOUT_TARGET")
        return LATEST_SCHEMA_VERSION
    if selected == "19" and confirmation == _SCHEMA19_CONFIRMATION:
        return SCHEMA19_RUNTIME_VERSION
    raise ValueError("SCHEMA19_RUNTIME_OPT_IN_REQUIRED")
