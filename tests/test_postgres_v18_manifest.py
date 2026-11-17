"""Full v18 source-structure gates; fixture has no private records."""
import json

import pytest

from amora.storage import postgres_v18_manifest as v18


def test_canonical_v18_structure_matches_live_pcs4c_aggregate_counts():
    manifest = v18.build_manifest()
    assert manifest["schema_version"] == 18
    assert manifest["sqlite_logical_table_count"] == 51
    assert manifest["postgres_relational_table_count"] == 49
    assert manifest["sqlite_sequence_table_count"] == 1
    assert manifest["source_fts5_table_count"] == 1
    assert manifest["explicit_index_count"] == 65
    assert (manifest["triggers"], manifest["views"]) == (0, 0)
    assert len(manifest["tables"]) == 51
    assert len(manifest["indexes"]) == 65
    assert all(t["sqlite_create_sql"] for t in manifest["tables"])


def test_manifest_has_reviewable_columns_keys_fks_and_indexes():
    manifest = v18.build_manifest()
    tables = {item["name"]: item for item in manifest["tables"]}
    assert [t["kind"] for t in manifest["tables"]].count("ordinary") == 49
    assert tables["sqlite_sequence"]["kind"] == "sqlite_sequence"
    assert tables["memory_fts"]["kind"] == "fts5"
    records = tables["memory_records"]
    cols = {c["name"]: c for c in records["columns"]}
    for name in ("id", "kind", "content", "summary", "owner_entity_id",
                 "privacy_scope", "audience_json", "metadata_json",
                 "remote_processing_allowed", "archived_at"):
        assert name in cols
    assert cols["id"]["postgres_type"] == "text"
    assert cols["remote_processing_allowed"]["postgres_type"] == "bigint"
    assert any(fk["parent"] == "events" and fk["columns"] == ["source_event_id"]
               for fk in records["foreign_keys"])
    assert any(fk["parent"] == "memory_records"
               for fk in tables["memory_entities"]["foreign_keys"])
    assert any(t["name"] == "memory_reflection_jobs" for t in manifest["tables"])
    assert any(t["name"] == "perceptual_expression_assets" for t in manifest["tables"])
    assert any("idx_memory" in i["name"] for i in manifest["indexes"])
    assert any("CHECK" in t["sqlite_create_sql"].upper()
               for t in manifest["tables"] if t["kind"] == "ordinary")


def test_manifest_deterministic_and_metadata_only():
    a = v18.build_manifest()
    b = v18.build_manifest()
    assert a["structural_sha256"] == b["structural_sha256"]
    summary = v18.sanitized_summary()
    assert summary["sqlite_logical_table_count"] == 51
    for secret in ("AMORA_POSTGRES_DSN", "AMORA_LIBSQL_URL", "PRIVATE KEY"):
        assert secret not in json.dumps(summary)
    assert not {"tables", "indexes", "structural_sha256"} & summary.keys()


def test_migration_sequence_guard_fails_closed(monkeypatch):
    monkeypatch.setattr(v18, "V18_MIGRATIONS", v18.V18_MIGRATIONS[:-1])
    with pytest.raises(v18.ManifestError, match="V18_MIGRATION_SEQUENCE_CHANGED"):
        v18.build_manifest()


def test_unreviewed_column_type_does_not_silently_map():
    import sqlite3
    with sqlite3.connect(":memory:") as c:
        c.execute("CREATE TABLE odd(id NUMERIC PRIMARY KEY)")
        with pytest.raises(v18.ManifestError, match="V18_UNREVIEWED_SOURCE_TYPE"):
            v18._column_inventory(c, "odd")


def test_canonical_migrations_have_clean_foreign_keys():
    with v18.synthetic_v18_connection() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchone() is None
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (18,)
        assert c.execute("SELECT COUNT(*) FROM memory_records").fetchone() == (0,)
