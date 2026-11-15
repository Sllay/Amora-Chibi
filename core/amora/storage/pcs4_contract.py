"""Shared PCS.4C job protocol: safe to import in the serving Core."""
STEPS = ("backup", "verify", "restore", "restart")
CONFIRMATIONS = {
    "backup": "SNAPSHOT CONSISTENTE",
    "verify": "VOLUME PRIVADO",
    "restore": "DESTINO VAZIO E INDEPENDENTE",
    "restart": "DESTINO ISOLADO",
}
MARKER = "PCS4C_JOB_REPORT "
REPORT_KEYS = frozenset({
    "schema_version", "tables", "rows", "assets", "equal_tables",
    "boots", "query_count", "rank_equal", "memory_tables_equal", "hit_counts",
    "private_hits", "archived_hits", "public_hits", "nonremote_hits",
})
