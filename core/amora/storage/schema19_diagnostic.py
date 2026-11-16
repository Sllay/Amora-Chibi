"""Bounded, content-free diagnostics for a rejected v18 test baseline.

No row values, IDs, SQL definitions, hashes, paths or credentials leave this
module. Unknown schema names are counted, never exported.
"""
from contextlib import closing
from pathlib import Path

from .pcs4_recovery import _open, _quote, _row_hashes, _schema, _tables

KNOWN_TABLES = frozenset("""
action_plans action_runs aliases autonomy_decisions autonomy_drives autonomy_intentions
avatar_asset_blobs avatar_assets avatar_projects cognitive_journal conversation_messages
conversation_runtime_sessions conversation_runtime_turns conversation_turn_decisions devices
drive_adjustments emotion_events entities entity_affect events memory_entities memory_fts
memory_links memory_records memory_reflection_invalidations memory_reflection_jobs memory_tags
messaging_audit messaging_conversation_floor messaging_inbound_generation messaging_outbound_queue
model_usage mood_state perceptual_expression_assets perceptual_expression_observations
perceptual_expression_senders person_profiles platform_identity_bindings platform_presence
relationship_revisions relationships schema_migrations self_entries sessions social_bids
speech_segments speech_utterances sqlite_sequence tool_executions world_claims world_evidence
""".split())

# Only known structural/metadata names, not arbitrary column identifiers.
KNOWN_COLUMNS = frozenset("""
id entity_id source target relation_type confidence canonical_name normalized_alias
payload_json profile_json fingerprint catalog_fingerprint catalog_revision revision
created_at updated_at last_seen timestamp applied_at version seq name
owner_person_id owner_entity_id device_type trust_level capabilities_json
content summary access_count last_accessed_at archived_at status value value_json
valid_from valid_until recorded_at observed_at kind source_event_id
""".split())


class BaselineDiverged(ValueError):
    def __init__(self, diagnostic):
        super().__init__("SCHEMA19_DESTINATION_DIVERGED")
        self.diagnostic = diagnostic


def _changed_columns(old, current, table):
    """Summarize changed columns only for rows with a common unique PK."""
    info = old.execute("PRAGMA table_info(" + _quote(table) + ")").fetchall()
    names = [r[1] for r in info]
    pk = sorted((r[5], i) for i, r in enumerate(info) if r[5])
    if not pk or not names:
        return [], 0
    indexes = [i for _, i in pk]

    def rows(db):
        result = {}
        for row in db.execute("SELECT * FROM " + _quote(table)):
            values = tuple(row)
            key = tuple(values[i] for i in indexes)
            if any(v is None for v in key) or key in result:
                return None
            result[key] = values
        return result

    before, after = rows(old), rows(current)
    if before is None or after is None:
        return [], 0
    changed = set()
    for key in before.keys() & after.keys():
        changed.update(names[i] for i, (a, b) in enumerate(zip(before[key], after[key]))
                       if type(a) is not type(b) or a != b)
    return sorted(changed & KNOWN_COLUMNS), len(changed - KNOWN_COLUMNS)


def baseline_diagnostic(original, baseline):
    """Describe logical drift without weakening the migration preflight."""
    with closing(_open(Path(original))) as old, closing(_open(Path(baseline))) as current:
        a, b = _schema(old), _schema(current)
        left, right = set(_tables(old)), set(_tables(current))
        common = left & right
        items = []
        unknown = 0
        for table in sorted(left | right):
            if table not in KNOWN_TABLES:
                unknown += 1
                continue
            entry = {"table": table, "schema_changed": a.get(table) != b.get(table)}
            if table not in common:
                entry["status"] = "missing" if table in left else "added"
                items.append(entry)
                continue
            if entry["schema_changed"]:
                entry["status"] = "schema"
                items.append(entry)
                continue
            before, after = _row_hashes(old, table), _row_hashes(current, table)
            if before == after:
                continue
            entry.update(status="rows", backup_rows=sum(before.values()),
                         destination_rows=sum(after.values()),
                         backup_only_rows=sum((before - after).values()),
                         destination_only_rows=sum((after - before).values()))
            columns, other = _changed_columns(old, current, table)
            entry.update(changed_columns=columns, other_changed_columns=other)
            items.append(entry)
        return {
            "schema_missing_objects": len(a.keys() - b.keys()),
            "schema_added_objects": len(b.keys() - a.keys()),
            "schema_changed_objects": sum(a[k] != b[k] for k in a.keys() & b.keys()),
            "unknown_tables": unknown, "differences": items,
        }


def safe_diagnostic(raw):
    """Validate the exact small report contract before exposing Job logs."""
    if not isinstance(raw, dict):
        raise ValueError("invalid baseline diagnostic")
    keys = ("schema_missing_objects", "schema_added_objects", "schema_changed_objects", "unknown_tables")
    def count(value):
        if type(value) is not int or not 0 <= value <= 10**12:
            raise ValueError("invalid diagnostic count")
        return value
    safe = {key: count(raw.get(key)) for key in keys}
    items = raw.get("differences")
    if not isinstance(items, list) or len(items) > len(KNOWN_TABLES):
        raise ValueError("invalid diagnostic tables")
    seen = set()
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid diagnostic table")
        table, status = item.get("table"), item.get("status")
        if (not isinstance(table, str) or table not in KNOWN_TABLES or table in seen
                or status not in {"missing", "added", "schema", "rows"}
                or type(item.get("schema_changed")) is not bool):
            raise ValueError("invalid diagnostic table")
        seen.add(table)
        entry = {"table": table, "status": status, "schema_changed": item["schema_changed"]}
        if status == "rows":
            for key in ("backup_rows", "destination_rows", "backup_only_rows",
                        "destination_only_rows", "other_changed_columns"):
                entry[key] = count(item.get(key))
            columns = item.get("changed_columns")
            if (not isinstance(columns, list) or len(columns) > len(KNOWN_COLUMNS)
                    or any(not isinstance(c, str) or c not in KNOWN_COLUMNS for c in columns)
                    or len(set(columns)) != len(columns)):
                raise ValueError("invalid diagnostic columns")
            entry["changed_columns"] = columns
        result.append(entry)
    safe["differences"] = result
    return safe
