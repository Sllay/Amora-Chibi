"""Reviewed SQL entrypoints for the PostgreSQL memory runtime candidate.

Domain-specific stable query names keep dynamic projection filters and BM25
formulas out of generic SQLite-to-PostgreSQL translation. No production
registration; all SQL targets the disposable v18 synthetic namespace.
"""
from __future__ import annotations

from .postgres_runtime_connection import PostgresRuntimeError, ReviewedStatement

SCHEMA = "amora_pg_v18_lab"
READY = "PG_MEMORY_V18_INDEX_READY"
COUNT = "PG_MEMORY_COUNT"
GET = "PG_MEMORY_GET"
PUT = "PG_MEMORY_PUT"
CREATE = "PG_MEMORY_CREATE"
DEL_ENTITIES = "PG_MEMORY_DELETE_ENTITIES"
ADD_ENTITIES = "PG_MEMORY_ADD_ENTITIES"
DEL_TAGS = "PG_MEMORY_DELETE_TAGS"
ADD_TAGS = "PG_MEMORY_ADD_TAGS"
UPSERT_SEARCH = "PG_MEMORY_UPSERT_SEARCH"
UPSERT_DOC = "PG_MEMORY_UPSERT_DOC"
DEL_POSTINGS = "PG_MEMORY_DELETE_POSTINGS"
ADD_POSTINGS = "PG_MEMORY_ADD_POSTINGS"
COUNT_POSTINGS = "PG_MEMORY_COUNT_POSTINGS"
BASELINE = "PG_MEMORY_BASELINE"
FTS_RANK = "PG_MEMORY_BM25_RANK"
GET_BULK = "PG_MEMORY_GET_BULK"
ENTITIES_BULK = "PG_MEMORY_ENTITIES_BULK"
TAGS_BULK = "PG_MEMORY_TAGS_BULK"
ARCHIVE = "PG_MEMORY_ARCHIVE"
DEGRADE = "PG_MEMORY_DEGRADE"
LINK = "PG_MEMORY_LINK"
ACCESS = "PG_MEMORY_ACCESS"
GENERATION_LEASE = "PG_MEMORY_GENERATION_LEASE"
REFLECTION_LEASE = "PG_MEMORY_REFLECTION_LEASE"
AUTHORIZE_REFLECTION = "PG_MEMORY_AUTHORIZE_REFLECTION"
OWNER_TURN_STATUS = "PG_MEMORY_OWNER_TURN_STATUS"
SHAREABLE_OWNER = "PG_MEMORY_SHAREABLE_OWNER"
OWNER_RECALL = "PG_MEMORY_OWNER_RECALL"
ENTITY_RANK = "PG_MEMORY_ENTITY_RANK"
REFLECTION_AUDIT_COUNT = "PG_MEMORY_REFLECTION_AUDIT_COUNT"
REFLECTION_AUDIT_ROWS = "PG_MEMORY_REFLECTION_AUDIT_ROWS"


def memory_statements(*, schema: str = SCHEMA) -> dict[str, ReviewedStatement]:
    if schema != SCHEMA:
        raise PostgresRuntimeError("PG_MEMORY_SCHEMA_NOT_REVIEWED")
    m: dict[str, ReviewedStatement] = {}
    def add(key: str, sql: str, n: int, cap: int = 1000,
            *, batch: bool = False) -> None:
        if key in m:
            raise PostgresRuntimeError("PG_MEMORY_DUPLICATE_QUERY")
        m[key] = ReviewedStatement(sql, n, cap, batch)

    add(READY, """
        SELECT to_regclass('amora_pg_v18_lab.fts_doc_tokens') IS NOT NULL
           AND to_regclass('amora_pg_v18_lab.fts_term_postings') IS NOT NULL
    """,0,1)
    add(COUNT, """
        SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records
        WHERE (%s=1 OR archived_at IS NULL)
    """,1,1)
    add(GET, "SELECT * FROM amora_pg_v18_lab.memory_records WHERE id=%s",1,1)

    cols = """id,kind,content,summary,provenance_kind,provenance_source,
        provenance_confidence,source_event_id,actor_id,session_id,derived_from_json,
        privacy_scope,owner_entity_id,audience_json,remote_processing_allowed,
        valid_from,valid_until,created_at,updated_at,importance,gate_score,
        retrievability,access_count,last_accessed_at,archived_at,metadata_json"""
    fields = """kind,content,summary,provenance_kind,provenance_source,
        provenance_confidence,source_event_id,actor_id,session_id,derived_from_json,
        privacy_scope,owner_entity_id,audience_json,remote_processing_allowed,
        valid_from,valid_until,updated_at,importance,gate_score,retrievability,
        access_count,last_accessed_at,archived_at,metadata_json"""
    update = ",".join(name.strip() + "=EXCLUDED." + name.strip()
                      for name in fields.replace("\n", "").split(","))
    add(PUT,
        "INSERT INTO amora_pg_v18_lab.memory_records ("+cols+") "
        "VALUES ("+",".join(["%s"]*26)+") "
        "ON CONFLICT(id) DO UPDATE SET "+update+
        " WHERE memory_records.owner_entity_id IS NOT DISTINCT "
        "FROM EXCLUDED.owner_entity_id",26)
    add(CREATE,
        "INSERT INTO amora_pg_v18_lab.memory_records ("+cols+") "
        "VALUES ("+",".join(["%s"]*26)+") "
        "ON CONFLICT(id) DO NOTHING",26)
    add(DEL_ENTITIES,"DELETE FROM amora_pg_v18_lab.memory_entities WHERE memory_id=%s",1)
    add(ADD_ENTITIES,"INSERT INTO amora_pg_v18_lab.memory_entities(memory_id,entity_id,confidence) VALUES (%s,%s,%s)",3,batch=True)
    add(DEL_TAGS,"DELETE FROM amora_pg_v18_lab.memory_tags WHERE memory_id=%s",1)
    add(ADD_TAGS,"INSERT INTO amora_pg_v18_lab.memory_tags(memory_id,tag) VALUES (%s,%s)",2,batch=True)
    add(UPSERT_SEARCH,"""INSERT INTO amora_pg_v18_lab.memory_search(memory_id,content,tags)
        VALUES (%s,%s,%s) ON CONFLICT(memory_id) DO UPDATE SET
        content=EXCLUDED.content,tags=EXCLUDED.tags""",3)
    add(UPSERT_DOC,"""INSERT INTO amora_pg_v18_lab.fts_doc_tokens(memory_id,document_length)
        VALUES (%s,%s) ON CONFLICT(memory_id) DO UPDATE SET
        document_length=EXCLUDED.document_length""",2)
    add(DEL_POSTINGS,"DELETE FROM amora_pg_v18_lab.fts_term_postings WHERE memory_id=%s",1)
    add(ADD_POSTINGS,"""INSERT INTO amora_pg_v18_lab.fts_term_postings(memory_id,term,frequency)
        VALUES (%s,%s,%s)""",3,batch=True)
    add(COUNT_POSTINGS,"""SELECT COALESCE(SUM(frequency),0)
        FROM amora_pg_v18_lab.fts_term_postings WHERE memory_id=%s""",1,1)

    # Apply privacy, audience and expiry filtering *before* the candidate cap.
    # All filters are bound parameters. The caller supplies null for no filter.
    eligibility = """
        (%s=1 OR m.archived_at IS NULL)
        AND (m.valid_until IS NULL OR m.valid_until >= %s)
        AND (%s::text[] IS NULL OR m.kind=ANY(%s::text[]))
        AND (%s::text IS NULL OR m.created_at >= %s)
        AND (%s::text IS NULL OR m.created_at <= %s)
        AND (%s=0 OR m.privacy_scope='PUBLIC')
        AND (%s=0 OR m.remote_processing_allowed=1)
        AND (
            %s::text IS NULL OR m.privacy_scope='PUBLIC'
            OR (
                m.privacy_scope NOT IN ('AUTH','SECRET')
                AND (
                    m.owner_entity_id=%s
                    OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(
                            m.audience_json::jsonb
                        ) AS audience(member) WHERE audience.member=%s
                    )
                    OR (
                        COALESCE(m.owner_entity_id,'')=''
                        AND jsonb_array_length(m.audience_json::jsonb)=0
                        AND m.actor_id=%s
                    )
                )
            )
        )
    """
    # 13 bound arguments in the eligibility block, by position.
    n = 14
    add(BASELINE, "SELECT m.* FROM amora_pg_v18_lab.memory_records m "
                  "WHERE "+eligibility+
                  " ORDER BY (m.importance*m.retrievability) DESC,"
                  " m.created_at DESC,m.id LIMIT 300",n,300)
    # PostgreSQL postings preserve the FTS5 corpus including archived entries.
    # Formula mirrors the existing lab's FTS5-BM25 (negative score sorts first).
    add(FTS_RANK,"""
        WITH query_terms AS (SELECT unnest(%s::text[]) AS term),
        corpus AS (
            SELECT COUNT(*)::float8 AS n,AVG(document_length)::float8 AS avgdl
            FROM amora_pg_v18_lab.fts_doc_tokens
        ),
        df AS (
            SELECT q.term,COUNT(p.memory_id)::float8 AS frequency
            FROM query_terms q LEFT JOIN amora_pg_v18_lab.fts_term_postings p
            ON p.term=q.term GROUP BY q.term
        ),
        scored AS (
            SELECT m.id,SUM(
                -GREATEST(0.000001::float8,
                    LN((c.n-df.frequency+0.5)/(df.frequency+0.5)))
                * (p.frequency::float8*2.2) /
                (p.frequency::float8+1.2*(0.25+0.75*
                    d.document_length::float8/c.avgdl))
            ) AS score
            FROM amora_pg_v18_lab.fts_term_postings p
            JOIN df ON df.term=p.term
            JOIN amora_pg_v18_lab.fts_doc_tokens d ON d.memory_id=p.memory_id
            JOIN amora_pg_v18_lab.memory_records m ON m.id=p.memory_id
            CROSS JOIN corpus c
            WHERE c.avgdl>0
                AND (m.archived_at IS NULL OR %s=1)
                AND (m.valid_until IS NULL OR m.valid_until >= %s)
                AND (%s::text[] IS NULL OR m.kind=ANY(%s::text[]))
                AND (%s::text IS NULL OR m.created_at >= %s)
                AND (%s::text IS NULL OR m.created_at <= %s)
                AND (%s=0 OR m.privacy_scope='PUBLIC')
                AND (%s=0 OR m.remote_processing_allowed=1)
                AND (%s::text IS NULL OR m.privacy_scope='PUBLIC' OR (
                    m.privacy_scope NOT IN ('AUTH','SECRET')
                    AND (m.owner_entity_id=%s OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(
                            m.audience_json::jsonb
                        ) AS audience(member) WHERE audience.member=%s
                    ) OR (COALESCE(m.owner_entity_id,'')=''
                        AND jsonb_array_length(m.audience_json::jsonb)=0
                        AND m.actor_id=%s))
                ))
                AND (%s::text[] IS NULL OR m.id=ANY(%s::text[]))
            GROUP BY m.id
        )
        SELECT id,score FROM scored ORDER BY score ASC,id LIMIT 150
    """,17,150)
    add(GET_BULK,"SELECT * FROM amora_pg_v18_lab.memory_records WHERE id=ANY(%s::text[])",1,450)
    add(ENTITIES_BULK,"""SELECT memory_id,entity_id,confidence
        FROM amora_pg_v18_lab.memory_entities
        WHERE memory_id=ANY(%s::text[]) ORDER BY memory_id,entity_id""",1,15000)
    add(TAGS_BULK,"""SELECT memory_id,tag FROM amora_pg_v18_lab.memory_tags
        WHERE memory_id=ANY(%s::text[]) ORDER BY memory_id,tag""",1,15000)
    add(ARCHIVE,"""UPDATE amora_pg_v18_lab.memory_records
        SET archived_at=%s,updated_at=%s WHERE id=%s AND archived_at IS NULL""",3)
    add(DEGRADE,"""UPDATE amora_pg_v18_lab.memory_records
        SET retrievability=%s,updated_at=%s WHERE id=%s""",3)
    add(LINK,"""INSERT INTO amora_pg_v18_lab.memory_links(
        source_memory_id,target_memory_id,link_type,confidence)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT(source_memory_id,target_memory_id,link_type)
        DO UPDATE SET confidence=EXCLUDED.confidence""",4)
    add(ACCESS,"""UPDATE amora_pg_v18_lab.memory_records
        SET access_count=access_count+1,last_accessed_at=%s
        WHERE id=ANY(%s::text[])""",2)
    add(GENERATION_LEASE, """
        SELECT 1 FROM amora_pg_v18_lab.messaging_inbound_generation g
        JOIN amora_pg_v18_lab.conversation_messages m
          ON m.turn_id=g.turn_id
         AND m.dedupe_key='cognitive-input:' || g.turn_id
        WHERE g.turn_id=%s AND g.lease_token=%s AND g.lease_until>%s
          AND m.speaker_id=%s AND m.session_id=g.session_id
          AND m.session_id=%s AND m.privacy_scope=%s
          AND (%s=0 OR (m.remote_processing_allowed=1
                         AND m.privacy_scope='PERSONAL'))
          AND m.metadata_json::jsonb ->> 'explicit_memory_request'
              IN ('1','true')
          AND m.metadata_json::jsonb ->> 'channel_type'='dm'
          AND NOT EXISTS (
              SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
              WHERE x.session_id=g.session_id
                AND x.native_message_hash=g.native_message_hash
                AND x.invalidated_through>=g.transport_revision
          )
    """,7,1)
    add(REFLECTION_LEASE, """
        SELECT 1 FROM amora_pg_v18_lab.memory_reflection_jobs
        WHERE turn_id=%s AND status='claimed'
          AND lease_token=%s AND lease_until>%s
    """,3,1)
    add(AUTHORIZE_REFLECTION, """
        UPDATE amora_pg_v18_lab.memory_records AS r
        SET remote_processing_allowed=1,owner_entity_id=%s,updated_at=%s
        WHERE r.id=%s AND r.privacy_scope='PERSONAL'
          AND r.archived_at IS NULL AND r.remote_processing_allowed=0
          AND r.provenance_source LIKE 'cognition:%%'
          AND r.provenance_kind='inference' AND r.actor_id='ENTITY_AMORA'
          AND (r.owner_entity_id IS NULL OR r.owner_entity_id=%s)
          AND NOT EXISTS(
              SELECT 1 FROM jsonb_array_elements_text(r.audience_json::jsonb)
              AS audience(value) WHERE audience.value<>%s
          )
          AND jsonb_array_length(r.derived_from_json::jsonb)=0
          AND EXISTS(
              SELECT 1 FROM amora_pg_v18_lab.conversation_messages m
              WHERE m.turn_id=r.metadata_json::jsonb ->> 'turn_id'
                AND m.dedupe_key='cognitive-input:' || m.turn_id
                AND m.session_id=r.session_id AND m.speaker_id=%s
                AND m.privacy_scope='PERSONAL'
                AND m.remote_processing_allowed=1
                AND m.metadata_json::jsonb ->> 'surface'='platform_messaging'
                AND m.metadata_json::jsonb ->> 'channel_type'='dm'
                AND COALESCE(m.metadata_json::jsonb ->> 'test_mode','0')
                    IN ('0','false')
                AND COALESCE(
                    m.metadata_json::jsonb ->> 'memory_consolidation','1'
                ) IN ('1','true')
                AND NOT EXISTS (
                    SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
                    WHERE x.session_id=m.session_id
                    AND x.native_message_hash=m.metadata_json::jsonb ->>
                        'native_message_id_hash'
                    AND x.invalidated_through>=COALESCE(
                        (m.metadata_json::jsonb ->> 'transport_revision')::BIGINT,1
                    )
                )
          )
    """,6)
    add(OWNER_TURN_STATUS, """
        SELECT id,privacy_scope,remote_processing_allowed,archived_at,
               provenance_kind,provenance_source,valid_from,valid_until
        FROM amora_pg_v18_lab.memory_records
        WHERE owner_entity_id=%s
          AND privacy_scope NOT IN ('AUTH','SECRET')
          AND metadata_json::jsonb ->> 'turn_id'=%s
        ORDER BY created_at,id LIMIT 50
    """,2,50)
    add(SHAREABLE_OWNER, """
        SELECT m.* FROM amora_pg_v18_lab.memory_records m
        WHERE m.owner_entity_id=%s AND m.privacy_scope='PERSONAL'
          AND m.archived_at IS NULL
          AND (m.valid_from IS NULL OR m.valid_from<=%s)
          AND (m.valid_until IS NULL OR m.valid_until>=%s)
          AND jsonb_array_length(m.derived_from_json::jsonb)=0
          AND NOT EXISTS(
              SELECT 1 FROM jsonb_array_elements_text(m.audience_json::jsonb)
              AS a(value) WHERE a.value<>m.owner_entity_id
          )
          AND (
              (m.provenance_kind='user_assertion'
               AND m.actor_id=m.owner_entity_id)
              OR (
                  m.provenance_kind='inference' AND m.actor_id='ENTITY_AMORA'
                  AND m.provenance_source LIKE 'cognition:%%'
                  AND EXISTS(
                      SELECT 1 FROM amora_pg_v18_lab.conversation_messages s
                      WHERE s.turn_id=m.metadata_json::jsonb ->> 'turn_id'
                        AND s.dedupe_key='cognitive-input:' || s.turn_id
                        AND s.speaker_id=m.owner_entity_id
                        AND s.session_id=m.session_id
                        AND s.privacy_scope='PERSONAL'
                        AND s.metadata_json::jsonb ->> 'surface'
                            ='platform_messaging'
                        AND s.metadata_json::jsonb ->> 'channel_type'='dm'
                        AND COALESCE(s.metadata_json::jsonb ->> 'test_mode','0')
                            IN ('0','false')
                        AND NOT EXISTS(
                            SELECT 1 FROM amora_pg_v18_lab.memory_reflection_invalidations x
                            WHERE x.session_id=s.session_id
                              AND x.native_message_hash=
                                  s.metadata_json::jsonb ->> 'native_message_id_hash'
                              AND x.invalidated_through>=COALESCE(
                                  (s.metadata_json::jsonb ->> 'transport_revision')::BIGINT,1
                              )
                        )
                  )
              )
          ) AND m.id=%s
          FOR SHARE OF m
    """,4,1)
    add(OWNER_RECALL, """
        SELECT m.* FROM amora_pg_v18_lab.memory_records m
        WHERE m.owner_entity_id=%s AND
            m.privacy_scope IN ('PERSONAL','PRIVATE','SOCIAL','PUBLIC')
            AND m.archived_at IS NULL
            AND (m.valid_from IS NULL OR m.valid_from<=%s)
            AND (m.valid_until IS NULL OR m.valid_until>=%s)
            AND (%s=0 OR m.privacy_scope='PUBLIC')
        ORDER BY m.created_at DESC,m.id DESC LIMIT %s OFFSET %s
    """,6,64)
    add(ENTITY_RANK, """
        SELECT memory_id,entity_id,confidence
        FROM amora_pg_v18_lab.memory_entities
        WHERE entity_id=ANY(%s::text[])
        ORDER BY confidence DESC,memory_id
    """,1,50000)
    add(REFLECTION_AUDIT_COUNT, """
        SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records
        WHERE archived_at IS NULL AND remote_processing_allowed=0
          AND provenance_source LIKE 'cognition:%%'
    """,0,1)
    add(REFLECTION_AUDIT_ROWS, """
        SELECT id,owner_entity_id,privacy_scope
        FROM amora_pg_v18_lab.memory_records
        WHERE archived_at IS NULL AND remote_processing_allowed=0
          AND provenance_source LIKE 'cognition:%%'
        ORDER BY created_at DESC LIMIT 100
    """,0,100)
    return m
