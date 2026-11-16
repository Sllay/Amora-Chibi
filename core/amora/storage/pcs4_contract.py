"""Shared PCS.4C job protocol: safe to import in the serving Core."""
STEPS = ("backup", "verify", "restore", "restart")
CONFIRMATIONS = {
    "backup": "SNAPSHOT CONSISTENTE",
    "verify": "VOLUME PRIVADO",
    "restore": "DESTINO VAZIO E INDEPENDENTE",
    "restart": "DESTINO ISOLADO",
    "review_restore": "REVISAR DESTINO ISOLADO",
}
MARKER = "PCS4C_JOB_REPORT "
REPORT_KEYS = frozenset({
    "schema_version", "tables", "rows", "assets", "equal_tables",
    "boots", "query_count", "rank_equal", "memory_tables_equal", "hit_counts",
    "private_hits", "archived_hits", "public_hits", "nonremote_hits",
})


SCHEMA19_STEPS = ("schema19_prepare", "schema19_restore", "schema19_restart")
SCHEMA19_ACTIONS = frozenset((*SCHEMA19_STEPS, "schema19_review_restore", "schema19_status"))
CONFIRMATIONS.update({
    "schema19_prepare": "PRESERVAR BACKUP V18",
    "schema19_restore": "NOVO DESTINO V19 VAZIO",
    "schema19_restart": "DESTINO V19 ISOLADO",
    "schema19_review_restore": "REVISAR DESTINO V19",
    "schema19_status": "CONSULTAR ESTADO V19",
})
SCHEMA19_REPORT_KEYS = REPORT_KEYS | frozenset({
    "source_schema_version", "old_tables", "preserved_tables", "new_tables",
    "migration_history_preserved", "episode_work_tables_equal",
})

SCHEMA19_ERROR_CODES = frozenset({
    "SCHEMA19_V18_NOT_CERTIFIED", "SCHEMA19_V18_DESTINATION_CHANGED",
    "SCHEMA19_DESTINATION_NOT_INDEPENDENT", "SCHEMA19_PIN_CHANGED",
    "SCHEMA19_CANDIDATE_CHANGED", "SCHEMA19_STEP_BLOCKED",
    "SCHEMA19_CONFIGURATION_REQUIRED", "SCHEMA19_REVIEW_REQUIRED",
})
