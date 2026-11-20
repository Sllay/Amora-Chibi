"""Explicit reviewed PostgreSQL 18 SQL for canonical expression memory.

Static SQL only, no runtime SQLite dialect translator. Source keys are the
exact normalized literal statements of ExpressionMemory. All tables target
the synthetic PG18 v18 namespace; only opt-in candidate mode uses this.
Changes to the canonical SQL must be reviewed and added here or fail closed.
"""
from __future__ import annotations

from .postgres_runtime_connection import ReviewedStatement


def expression_statements() -> dict[str, ReviewedStatement]:
    statements: dict[str, ReviewedStatement] = {}
    # Each entry was individually inventoried from the 22 static canonical
    # SELECT/UPDATE/INSERT statements and targets one explicit PG table.
    entries = (
        # 1: SELECT * FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND scope_id=? 
        ("SELECT * FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=? LIMIT 1",
         "SELECT * FROM amora_pg_v18_lab.perceptual_expression_assets WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s LIMIT 1", 5, 1),
        # 2: SELECT * FROM perceptual_expression_assets WHERE semantic_status IN ('metadata_only','pending_v
        ("SELECT * FROM perceptual_expression_assets WHERE semantic_status IN ('metadata_only','pending_visual','stale','failed') OR last_visual_analysis_content_fingerprint IS NULL OR ( content_fingerprint IS NOT NULL AND last_visual_analysis_content_fingerprint <> content_fingerprint ) ORDER BY last_seen_at DESC LIMIT ?",
         "SELECT * FROM amora_pg_v18_lab.perceptual_expression_assets WHERE semantic_status IN ('metadata_only','pending_visual','stale','failed') OR last_visual_analysis_content_fingerprint IS NULL OR ( content_fingerprint IS NOT NULL AND last_visual_analysis_content_fingerprint <> content_fingerprint ) ORDER BY last_seen_at DESC LIMIT %s", 1, 512),
        # 3: SELECT * FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND ( semantic_
        ("SELECT * FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND ( semantic_status IN ('metadata_only','pending_visual','stale','failed') OR last_visual_analysis_content_fingerprint IS NULL OR ( content_fingerprint IS NOT NULL AND last_visual_analysis_content_fingerprint <> content_fingerprint ) ) AND (visual_defer_until IS NULL OR visual_defer_until <= ?) AND ( visual_claim_token IS NULL OR visual_claimed_until IS NULL OR visual_claimed_until <= ? ) ORDER BY last_seen_at DESC LIMIT ?",
         "SELECT * FROM amora_pg_v18_lab.perceptual_expression_assets WHERE platform=%s AND account_scope=%s AND ( semantic_status IN ('metadata_only','pending_visual','stale','failed') OR last_visual_analysis_content_fingerprint IS NULL OR ( content_fingerprint IS NOT NULL AND last_visual_analysis_content_fingerprint <> content_fingerprint ) ) AND (visual_defer_until IS NULL OR visual_defer_until <= %s) AND ( visual_claim_token IS NULL OR visual_claimed_until IS NULL OR visual_claimed_until <= %s ) ORDER BY last_seen_at DESC LIMIT %s", 5, 512),
        # 4: UPDATE perceptual_expression_assets SET visual_claim_token=?, visual_claimed_until=?, visual_at
        ("UPDATE perceptual_expression_assets SET visual_claim_token=?, visual_claimed_until=?, visual_attempt_count=visual_attempt_count+1, visual_last_error_code=NULL WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=? AND fingerprint=? AND (visual_defer_until IS NULL OR visual_defer_until <= ?) AND ( visual_claim_token IS NULL OR visual_claimed_until IS NULL OR visual_claimed_until <= ? )",
         "UPDATE amora_pg_v18_lab.perceptual_expression_assets SET visual_claim_token=%s, visual_claimed_until=%s, visual_attempt_count=visual_attempt_count+1, visual_last_error_code=NULL WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s AND fingerprint=%s AND (visual_defer_until IS NULL OR visual_defer_until <= %s) AND ( visual_claim_token IS NULL OR visual_claimed_until IS NULL OR visual_claimed_until <= %s )", 10, 1000),
        # 5: SELECT * FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND scope_id=? 
        ("SELECT * FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?",
         "SELECT * FROM amora_pg_v18_lab.perceptual_expression_assets WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s", 5, 1),
        # 6: SELECT fingerprint, semantic_profile_json, semantic_revision, last_visual_analysis_content_fing
        ("SELECT fingerprint, semantic_profile_json, semantic_revision, last_visual_analysis_content_fingerprint, visual_claim_token, visual_claimed_until FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?",
         "SELECT fingerprint, semantic_profile_json, semantic_revision, last_visual_analysis_content_fingerprint, visual_claim_token, visual_claimed_until FROM amora_pg_v18_lab.perceptual_expression_assets WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s", 5, 1),
        # 7: UPDATE perceptual_expression_assets SET content_fingerprint=?, content_mime=?, content_size_byt
        ("UPDATE perceptual_expression_assets SET content_fingerprint=?, content_mime=?, content_size_bytes=?, semantic_status='enriched', last_visual_analysis_fingerprint=?, visual_claim_token=NULL, visual_claimed_until=NULL, visual_last_error_code=NULL, visual_defer_until=NULL WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=? AND fingerprint=? AND visual_claim_token=?",
         "UPDATE amora_pg_v18_lab.perceptual_expression_assets SET content_fingerprint=%s, content_mime=%s, content_size_bytes=%s, semantic_status='enriched', last_visual_analysis_fingerprint=%s, visual_claim_token=NULL, visual_claimed_until=NULL, visual_last_error_code=NULL, visual_defer_until=NULL WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s AND fingerprint=%s AND visual_claim_token=%s", 11, 1000),
        # 8: UPDATE perceptual_expression_assets SET content_fingerprint=?, content_mime=?, content_size_byt
        ("UPDATE perceptual_expression_assets SET content_fingerprint=?, content_mime=?, content_size_bytes=? WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=? AND fingerprint=? AND visual_claim_token=?",
         "UPDATE amora_pg_v18_lab.perceptual_expression_assets SET content_fingerprint=%s, content_mime=%s, content_size_bytes=%s WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s AND fingerprint=%s AND visual_claim_token=%s", 10, 1000),
        # 9: SELECT fingerprint, semantic_status, content_fingerprint, last_visual_analysis_content_fingerpr
        ("SELECT fingerprint, semantic_status, content_fingerprint, last_visual_analysis_content_fingerprint, visual_claim_token, visual_claimed_until FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?",
         "SELECT fingerprint, semantic_status, content_fingerprint, last_visual_analysis_content_fingerprint, visual_claim_token, visual_claimed_until FROM amora_pg_v18_lab.perceptual_expression_assets WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s", 5, 1),
        # 10: UPDATE perceptual_expression_assets SET semantic_profile_json=?, semantic_status='enriched', se
        ("UPDATE perceptual_expression_assets SET semantic_profile_json=?, semantic_status='enriched', semantic_revision=semantic_revision+1, last_visual_analysis_at=?, last_visual_analysis_fingerprint=?, last_visual_analysis_content_fingerprint=?, visual_provider=?, visual_model=?, visual_claim_token=NULL, visual_claimed_until=NULL, visual_last_error_code=NULL, visual_defer_until=NULL WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=? AND fingerprint=? AND content_fingerprint=? AND visual_claim_token=?",
         "UPDATE amora_pg_v18_lab.perceptual_expression_assets SET semantic_profile_json=%s, semantic_status='enriched', semantic_revision=semantic_revision+1, last_visual_analysis_at=%s, last_visual_analysis_fingerprint=%s, last_visual_analysis_content_fingerprint=%s, visual_provider=%s, visual_model=%s, visual_claim_token=NULL, visual_claimed_until=NULL, visual_last_error_code=NULL, visual_defer_until=NULL WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s AND fingerprint=%s AND content_fingerprint=%s AND visual_claim_token=%s", 14, 1000),
        # 11: SELECT fingerprint, visual_claim_token, visual_claimed_until FROM perceptual_expression_assets 
        ("SELECT fingerprint, visual_claim_token, visual_claimed_until FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?",
         "SELECT fingerprint, visual_claim_token, visual_claimed_until FROM amora_pg_v18_lab.perceptual_expression_assets WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s", 5, 1),
        # 12: UPDATE perceptual_expression_assets SET semantic_status='failed', visual_claim_token=NULL, visu
        ("UPDATE perceptual_expression_assets SET semantic_status='failed', visual_claim_token=NULL, visual_claimed_until=NULL, visual_last_error_code=?, visual_defer_until=? WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=? AND fingerprint=? AND visual_claim_token=?",
         "UPDATE amora_pg_v18_lab.perceptual_expression_assets SET semantic_status='failed', visual_claim_token=NULL, visual_claimed_until=NULL, visual_last_error_code=%s, visual_defer_until=%s WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s AND fingerprint=%s AND visual_claim_token=%s", 9, 1000),
        # 13: SELECT fingerprint, semantic_profile_json, semantic_status FROM perceptual_expression_assets WH
        ("SELECT fingerprint, semantic_profile_json, semantic_status FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?",
         "SELECT fingerprint, semantic_profile_json, semantic_status FROM amora_pg_v18_lab.perceptual_expression_assets WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s", 5, 1),
        # 14: INSERT INTO perceptual_expression_assets( platform, account_scope, scope_id, kind, native_id, n
        ("INSERT INTO perceptual_expression_assets( platform, account_scope, scope_id, kind, native_id, name, description, tags_json, semantic_profile_json, semantic_status, fingerprint, fingerprint_source, semantic_revision, first_seen_at, last_seen_at, seen_count, unique_senders_count, last_sender_ref, last_source_message_id, metadata_json ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_visual', ?, 'transport_metadata', 0, ?, ?, 0, 0, ?, ?, ?)",
         "INSERT INTO amora_pg_v18_lab.perceptual_expression_assets( platform, account_scope, scope_id, kind, native_id, name, description, tags_json, semantic_profile_json, semantic_status, fingerprint, fingerprint_source, semantic_revision, first_seen_at, last_seen_at, seen_count, unique_senders_count, last_sender_ref, last_source_message_id, metadata_json ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending_visual', %s, 'transport_metadata', 0, %s, %s, 0, 0, %s, %s, %s)", 15, 1000),
        # 15: UPDATE perceptual_expression_assets SET name=?, description=?, tags_json=?, semantic_profile_js
        ("UPDATE perceptual_expression_assets SET name=?, description=?, tags_json=?, semantic_profile_json=?, semantic_status=?, fingerprint=?, fingerprint_source='transport_metadata', last_seen_at=?, last_sender_ref=?, last_source_message_id=?, visual_claim_token=CASE WHEN ? THEN NULL ELSE visual_claim_token END, visual_claimed_until=CASE WHEN ? THEN NULL ELSE visual_claimed_until END, visual_defer_until=CASE WHEN ? THEN NULL ELSE visual_defer_until END, visual_last_error_code=CASE WHEN ? THEN NULL ELSE visual_last_error_code END WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?",
         "UPDATE amora_pg_v18_lab.perceptual_expression_assets SET name=%s, description=%s, tags_json=%s, semantic_profile_json=%s, semantic_status=%s, fingerprint=%s, fingerprint_source='transport_metadata', last_seen_at=%s, last_sender_ref=%s, last_source_message_id=%s, visual_claim_token=CASE WHEN %s=1 THEN NULL ELSE visual_claim_token END, visual_claimed_until=CASE WHEN %s=1 THEN NULL ELSE visual_claimed_until END, visual_defer_until=CASE WHEN %s=1 THEN NULL ELSE visual_defer_until END, visual_last_error_code=CASE WHEN %s=1 THEN NULL ELSE visual_last_error_code END WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s", 18, 1000),
        # 16: INSERT INTO perceptual_expression_observations( platform, account_scope, scope_id, kind, native
        ("INSERT INTO perceptual_expression_observations( platform, account_scope, scope_id, kind, native_id, source_message_id, sender_ref, observed_at ) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, account_scope, scope_id, kind, native_id, source_message_id) DO NOTHING",
         "INSERT INTO amora_pg_v18_lab.perceptual_expression_observations( platform, account_scope, scope_id, kind, native_id, source_message_id, sender_ref, observed_at ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(platform, account_scope, scope_id, kind, native_id, source_message_id) DO NOTHING", 8, 1000),
        # 17: SELECT COUNT(*) AS seen_count, COUNT(DISTINCT sender_ref) AS sender_count, MAX(observed_at) AS 
        ("SELECT COUNT(*) AS seen_count, COUNT(DISTINCT sender_ref) AS sender_count, MAX(observed_at) AS last_seen FROM perceptual_expression_observations WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?",
         "SELECT COUNT(*) AS seen_count, COUNT(DISTINCT sender_ref) AS sender_count, MAX(observed_at) AS last_seen FROM amora_pg_v18_lab.perceptual_expression_observations WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s", 5, 1),
        # 18: SELECT COUNT(*) AS seen_count, MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen FR
        ("SELECT COUNT(*) AS seen_count, MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen FROM perceptual_expression_observations WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=? AND sender_ref=?",
         "SELECT COUNT(*) AS seen_count, MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen FROM amora_pg_v18_lab.perceptual_expression_observations WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s AND sender_ref=%s", 6, 1),
        # 19: INSERT INTO perceptual_expression_senders( platform, account_scope, scope_id, kind, native_id, 
        ("INSERT INTO perceptual_expression_senders( platform, account_scope, scope_id, kind, native_id, sender_ref, first_seen_at, last_seen_at, seen_count ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, account_scope, scope_id, kind, native_id, sender_ref) DO UPDATE SET first_seen_at=excluded.first_seen_at, last_seen_at=excluded.last_seen_at, seen_count=excluded.seen_count",
         "INSERT INTO amora_pg_v18_lab.perceptual_expression_senders( platform, account_scope, scope_id, kind, native_id, sender_ref, first_seen_at, last_seen_at, seen_count ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(platform, account_scope, scope_id, kind, native_id, sender_ref) DO UPDATE SET first_seen_at=excluded.first_seen_at, last_seen_at=excluded.last_seen_at, seen_count=excluded.seen_count", 9, 1000),
        # 20: UPDATE perceptual_expression_assets SET seen_count=?, unique_senders_count=?, last_seen_at=? WH
        ("UPDATE perceptual_expression_assets SET seen_count=?, unique_senders_count=?, last_seen_at=? WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?",
         "UPDATE amora_pg_v18_lab.perceptual_expression_assets SET seen_count=%s, unique_senders_count=%s, last_seen_at=%s WHERE platform=%s AND account_scope=%s AND scope_id=%s AND kind=%s AND native_id=%s", 8, 1000),
        # 21: SELECT * FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND scope_id=? 
        ("SELECT * FROM perceptual_expression_assets WHERE platform=? AND account_scope=? AND scope_id=? ORDER BY last_seen_at DESC LIMIT ?",
         "SELECT * FROM amora_pg_v18_lab.perceptual_expression_assets WHERE platform=%s AND account_scope=%s AND scope_id=%s ORDER BY last_seen_at DESC LIMIT %s", 4, 1024),
        # 22: SELECT COUNT(*) FROM perceptual_expression_assets
        ("SELECT COUNT(*) FROM perceptual_expression_assets",
         "SELECT COUNT(*) FROM amora_pg_v18_lab.perceptual_expression_assets", 0, 1),
    )
    for key, pg, count, cap in entries:
        if key in statements:
            raise ValueError("PG_EXPRESSION_DUPLICATE_SQL")
        statements[key] = ReviewedStatement(pg, count, cap)
    return statements
