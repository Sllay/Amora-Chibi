"""Reviewed PostgreSQL v18 statements for original WorldStore / self-model."""
from __future__ import annotations

from .postgres_runtime_connection import ReviewedStatement

CLAIM_QUERY="PG_WORLD_QUERY"
SELF_LIST="PG_WORLD_SELF_LIST"
REL_HISTORY="PG_WORLD_REL_HISTORY"
EVIDENCE_BULK="PG_WORLD_EVIDENCE_BULK"

def world_statements() -> dict[str,ReviewedStatement]:
    m:dict[str,ReviewedStatement]={}
    def add(src,pg,n,cap=1000,*,batch=False):
        key=" ".join(src.split())
        if key in m:
            raise ValueError("PG_WORLD_DUPLICATE_QUERY")
        m[key]=ReviewedStatement(pg,n,cap,batch)
    add("SELECT COUNT(*) FROM world_claims",
        "SELECT COUNT(*) FROM amora_pg_v18_lab.world_claims",0,1)
    add("""INSERT INTO world_claims(
        id, kind, statement, subject_id, predicate, object_json, context_key,
        source, source_entity_id, confidence, valid_from, valid_until,
        created_at, updated_at, canonical, supersedes_claim_id, privacy_scope,
        remote_processing_allowed, metadata_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        """INSERT INTO amora_pg_v18_lab.world_claims(
        id,kind,statement,subject_id,predicate,object_json,context_key,
        source,source_entity_id,confidence,valid_from,valid_until,
        created_at,updated_at,canonical,supersedes_claim_id,privacy_scope,
        remote_processing_allowed,metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",19)
    add("""INSERT INTO world_evidence(claim_id, ordinal, kind, ref_id, confidence, note)
        VALUES (?, ?, ?, ?, ?, ?)""",
        """INSERT INTO amora_pg_v18_lab.world_evidence(
        claim_id,ordinal,kind,ref_id,confidence,note)
        VALUES (%s,%s,%s,%s,%s,%s)""",6,batch=True)
    add("SELECT * FROM world_claims WHERE id = ?",
        "SELECT * FROM amora_pg_v18_lab.world_claims WHERE id=%s",1,1)
    add("""UPDATE world_claims SET valid_until = ?, updated_at = ?
        WHERE id = ? AND (valid_until IS NULL OR valid_until > ?)""",
        """UPDATE amora_pg_v18_lab.world_claims
        SET valid_until=%s,updated_at=%s
        WHERE id=%s AND (valid_until IS NULL OR valid_until>%s)""",4)
    add("""SELECT * FROM world_claims
        WHERE kind = 'belief' AND canonical = 1
          AND subject_id IS ? AND predicate = ? AND context_key = ?
          AND valid_from <= ?
          AND (valid_until IS NULL OR valid_until > ?)
        ORDER BY valid_from DESC, created_at DESC
        LIMIT 1""",
        """SELECT * FROM amora_pg_v18_lab.world_claims
        WHERE kind='belief' AND canonical=1
          AND subject_id IS NOT DISTINCT FROM %s AND predicate=%s
          AND context_key=%s AND valid_from<=%s
          AND (valid_until IS NULL OR valid_until>%s)
        ORDER BY valid_from DESC,created_at DESC LIMIT 1""",5,1)
    add(CLAIM_QUERY,"""SELECT * FROM amora_pg_v18_lab.world_claims
        WHERE (%s::text IS NULL OR subject_id=%s)
          AND (%s::text IS NULL OR predicate=%s)
          AND (%s::text IS NULL OR context_key=%s)
          AND (%s::text[] IS NULL OR kind=ANY(%s::text[]))
          AND (%s=FALSE OR canonical=1)
          AND (%s=TRUE OR (
            valid_from<=%s AND (valid_until IS NULL OR valid_until>%s)
          ))
        ORDER BY valid_from DESC,created_at DESC LIMIT %s""",13,500)
    add("SELECT id, created_at FROM self_entries WHERE layer = ? AND key = ?",
        "SELECT id,created_at FROM amora_pg_v18_lab.self_entries WHERE layer=%s AND key=%s FOR UPDATE",2,1)
    add("""INSERT INTO self_entries(id, layer, key, value_json, confidence, source, locked, created_at, updated_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(layer, key) DO UPDATE SET
            value_json=excluded.value_json,
            confidence=excluded.confidence,
            source=excluded.source,
            locked=excluded.locked,
            updated_at=excluded.updated_at,
            metadata_json=excluded.metadata_json""",
        """INSERT INTO amora_pg_v18_lab.self_entries(
        id,layer,key,value_json,confidence,source,locked,created_at,updated_at,metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(layer,key) DO UPDATE SET
            value_json=EXCLUDED.value_json,confidence=EXCLUDED.confidence,
            source=EXCLUDED.source,locked=EXCLUDED.locked,
            updated_at=EXCLUDED.updated_at,metadata_json=EXCLUDED.metadata_json""",10)
    add("SELECT * FROM self_entries WHERE layer = ? AND key = ?",
        "SELECT * FROM amora_pg_v18_lab.self_entries WHERE layer=%s AND key=%s",2,1)
    add(SELF_LIST,"""SELECT * FROM amora_pg_v18_lab.self_entries
        WHERE (%s::text IS NULL OR layer=%s) ORDER BY layer,key""",2,10000)
    add("""INSERT INTO relationship_revisions(
        id, source_id, relation_type, target_id, confidence, source_of_truth,
        valid_from, valid_until, created_at, supersedes_id, metadata_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        """INSERT INTO amora_pg_v18_lab.relationship_revisions(
        id,source_id,relation_type,target_id,confidence,source_of_truth,
        valid_from,valid_until,created_at,supersedes_id,metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",11)
    add("""UPDATE relationship_revisions SET valid_until = ?
        WHERE id = ? AND (valid_until IS NULL OR valid_until > ?)""",
        """UPDATE amora_pg_v18_lab.relationship_revisions SET valid_until=%s
        WHERE id=%s AND (valid_until IS NULL OR valid_until>%s)""",3)
    add("""SELECT * FROM relationship_revisions
        WHERE source_id = ? AND relation_type = ? AND target_id = ?
          AND valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)
        ORDER BY valid_from DESC, created_at DESC LIMIT 1""",
        """SELECT * FROM amora_pg_v18_lab.relationship_revisions
        WHERE source_id=%s AND relation_type=%s AND target_id=%s
          AND valid_from<=%s AND (valid_until IS NULL OR valid_until>%s)
        ORDER BY valid_from DESC,created_at DESC LIMIT 1""",5,1)
    add(REL_HISTORY,"""SELECT * FROM amora_pg_v18_lab.relationship_revisions
        WHERE source_id=%s AND (%s::text IS NULL OR target_id=%s)
        ORDER BY valid_from DESC,created_at DESC""",3,10000)
    add(EVIDENCE_BULK,"""SELECT claim_id,kind,ref_id,confidence,note
        FROM amora_pg_v18_lab.world_evidence
        WHERE claim_id=ANY(%s::text[]) ORDER BY claim_id,ordinal""",1,15000)
    add("SELECT kind, ref_id, confidence, note FROM world_evidence WHERE claim_id = ? ORDER BY ordinal",
        """SELECT kind,ref_id,confidence,note
        FROM amora_pg_v18_lab.world_evidence WHERE claim_id=%s ORDER BY ordinal""",1,1000)
    return m
