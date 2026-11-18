"""Experimental PG18 SQL for the opt-in sensory-episode queue candidate.

These are reviewed v19 *candidate* statements targeting extra tables in a
disposable v18 PostgreSQL lab, not a production v19 schema migration.
Neither the tables nor the job worker are automatically activated.
"""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement

EPISODE_READY="PG_EPISODE_TABLES_READY"
EPISODE_FENCE_JOB="PG_EPISODE_FENCE_JOB"
EPISODE_FENCE_EVIDENCE="PG_EPISODE_FENCE_EVIDENCE"
EPISODE_FENCE_MEMORY="PG_EPISODE_FENCE_MEMORY"
EPISODE_FENCE_COMPLETE="PG_EPISODE_FENCE_COMPLETE"


def episode_statements()->dict[str,ReviewedStatement]:
    r:dict[str,ReviewedStatement]={}
    def add(k,sql,n,cap=1000,*,batch=False):
        key=" ".join(k.split())
        if key in r: raise ValueError("PG_EPISODE_DUPLICATE_SQL")
        r[key]=ReviewedStatement(sql,n,cap,batch)
    def alias(src,dst,n,cap=1000,*,batch=False):
        add(src,dst,n,cap,batch=batch)
    add(EPISODE_READY,"""SELECT to_regclass('amora_pg_v18_lab.sensory_episode_jobs') IS NOT NULL
        AND to_regclass('amora_pg_v18_lab.sensory_episode_job_evidence') IS NOT NULL
        AND to_regclass('amora_pg_v18_lab.sensory_episode_invalidations') IS NOT NULL""",0,1)
    alias("SELECT 1 FROM sensory_episode_invalidations WHERE event_id=?",
        "SELECT 1 FROM amora_pg_v18_lab.sensory_episode_invalidations WHERE event_id=%s",1,1)
    alias("SELECT 1 FROM sensory_episode_jobs WHERE job_id=?",
        "SELECT 1 FROM amora_pg_v18_lab.sensory_episode_jobs WHERE job_id=%s",1,1)
    alias("SELECT COUNT(*) FROM sensory_episode_jobs WHERE status IN ('pending','claimed')",
        """SELECT COUNT(*) FROM amora_pg_v18_lab.sensory_episode_jobs
        WHERE status IN ('pending','claimed')""",0,1)
    alias("SELECT COUNT(*) FROM sensory_episode_jobs WHERE correlation_id=? AND status IN ('pending','claimed')",
        """SELECT COUNT(*) FROM amora_pg_v18_lab.sensory_episode_jobs
        WHERE correlation_id=%s AND status IN ('pending','claimed')""",1,1)
    alias(
        "INSERT INTO sensory_episode_jobs(job_id,selection_json,session_id,correlation_id,"
        "status,created_at,available_at) VALUES (?,?,?,?,'pending',?,?) "
        "ON CONFLICT(job_id) DO NOTHING",
        """INSERT INTO amora_pg_v18_lab.sensory_episode_jobs(
            job_id,selection_json,session_id,correlation_id,status,
            created_at,available_at)
        VALUES (%s,%s,%s,%s,'pending',%s,%s)
        ON CONFLICT(job_id) DO NOTHING""",6)
    alias("INSERT INTO sensory_episode_job_evidence(job_id,event_id) VALUES (?,?)",
        """INSERT INTO amora_pg_v18_lab.sensory_episode_job_evidence(job_id,event_id)
        VALUES (%s,%s)""",2,batch=True)
    alias(
        "UPDATE sensory_episode_jobs SET status='failed',lease_token=NULL,lease_until=NULL,"
        "last_reason='EPISODE_ATTEMPTS_EXHAUSTED' WHERE job_id IN ("
        "SELECT job_id FROM sensory_episode_jobs WHERE status='claimed' "
        "AND lease_until<=? AND attempts>=? ORDER BY lease_until,job_id LIMIT 32)",
        """UPDATE amora_pg_v18_lab.sensory_episode_jobs
        SET status='failed',lease_token=NULL,lease_until=NULL,
            last_reason='EPISODE_ATTEMPTS_EXHAUSTED'
        WHERE job_id IN (
          SELECT job_id FROM amora_pg_v18_lab.sensory_episode_jobs
          WHERE status='claimed' AND lease_until<=%s AND attempts>=%s
          ORDER BY lease_until,job_id LIMIT 32 FOR UPDATE SKIP LOCKED
        )""",2)
    alias(
        "SELECT job_id,selection_json,session_id,correlation_id,attempts FROM sensory_episode_jobs "
        "WHERE attempts<? AND ((status='pending' AND available_at<=?) OR "
        "(status='claimed' AND lease_until<=?)) ORDER BY available_at,created_at,job_id LIMIT 1",
        """SELECT job_id,selection_json,session_id,correlation_id,attempts
        FROM amora_pg_v18_lab.sensory_episode_jobs
        WHERE attempts<%s AND
          ((status='pending' AND available_at<=%s)
             OR (status='claimed' AND lease_until<=%s))
        ORDER BY available_at,created_at,job_id
        LIMIT 1 FOR UPDATE SKIP LOCKED""",3,1)
    alias("SELECT event_id FROM sensory_episode_job_evidence WHERE job_id=?",
        """SELECT event_id FROM amora_pg_v18_lab.sensory_episode_job_evidence
        WHERE job_id=%s""",1,4)
    alias(
        "UPDATE sensory_episode_jobs SET status='failed',lease_token=NULL,lease_until=NULL,"
        "last_reason='EPISODE_WORK_CORRUPT' WHERE job_id=?",
        """UPDATE amora_pg_v18_lab.sensory_episode_jobs
        SET status='failed',lease_token=NULL,lease_until=NULL,
            last_reason='EPISODE_WORK_CORRUPT' WHERE job_id=%s""",1)
    alias(
        "UPDATE sensory_episode_jobs SET status='claimed',attempts=attempts+1,"
        "lease_token=?,lease_until=? WHERE job_id=? AND attempts=? AND attempts<? AND "
        "((status='pending' AND available_at<=?) OR (status='claimed' AND lease_until<=?))",
        """UPDATE amora_pg_v18_lab.sensory_episode_jobs SET
            status='claimed',attempts=attempts+1,lease_token=%s,lease_until=%s
        WHERE job_id=%s AND attempts=%s AND attempts<%s
          AND ((status='pending' AND available_at<=%s)
               OR (status='claimed' AND lease_until<=%s))""",7)
    alias(
        "UPDATE sensory_episode_jobs SET status=?,available_at=?,lease_token=NULL,lease_until=NULL,"
        "last_reason=? WHERE job_id=? AND status='claimed' AND lease_token=? "
        "AND attempts=? AND lease_until>? AND selection_json=? AND session_id=? AND correlation_id=?",
        """UPDATE amora_pg_v18_lab.sensory_episode_jobs SET
            status=%s,available_at=%s,lease_token=NULL,lease_until=NULL,
            last_reason=%s WHERE job_id=%s AND status='claimed'
            AND lease_token=%s AND attempts=%s AND lease_until>%s
            AND selection_json=%s AND session_id=%s AND correlation_id=%s""",10)
    alias(
        "UPDATE sensory_episode_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,"
        "last_reason='EPISODE_WORK_CANCELLED' WHERE job_id=? AND status IN ('pending','claimed')",
        """UPDATE amora_pg_v18_lab.sensory_episode_jobs SET
            status='cancelled',lease_token=NULL,lease_until=NULL,
            last_reason='EPISODE_WORK_CANCELLED'
        WHERE job_id=%s AND status IN ('pending','claimed')""",1)
    alias("SELECT job_id FROM sensory_episode_job_evidence WHERE event_id=? LIMIT 129",
        """SELECT job_id FROM amora_pg_v18_lab.sensory_episode_job_evidence
        WHERE event_id=%s LIMIT 129""",1,129)
    alias(
        "SELECT id FROM memory_records WHERE provenance_source='perception:episode:v1' "
        "AND archived_at IS NULL AND EXISTS (SELECT 1 FROM "
        "json_each(memory_records.metadata_json,'$.evidence_event_ids') WHERE value=?) LIMIT 129",
        """SELECT id FROM amora_pg_v18_lab.memory_records m
        WHERE provenance_source='perception:episode:v1'
          AND archived_at IS NULL
          AND EXISTS(
            SELECT 1 FROM jsonb_array_elements_text(
              m.metadata_json::jsonb -> 'evidence_event_ids'
            ) AS evidence(value) WHERE evidence.value=%s
          )
        LIMIT 129""",1,129)
    alias("INSERT INTO sensory_episode_invalidations(event_id,invalidated_at) VALUES (?,?) ON CONFLICT(event_id) DO NOTHING",
        """INSERT INTO amora_pg_v18_lab.sensory_episode_invalidations(
            event_id,invalidated_at)
        VALUES (%s,%s) ON CONFLICT(event_id) DO NOTHING""",2)
    alias(
        "UPDATE sensory_episode_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,"
        "last_reason='EPISODE_EVIDENCE_INVALIDATED' WHERE job_id=?",
        """UPDATE amora_pg_v18_lab.sensory_episode_jobs
        SET status='cancelled',lease_token=NULL,lease_until=NULL,
            last_reason='EPISODE_EVIDENCE_INVALIDATED' WHERE job_id=%s""",1,batch=True)
    alias("UPDATE memory_records SET archived_at=?,updated_at=? WHERE id=? AND archived_at IS NULL",
        """UPDATE amora_pg_v18_lab.memory_records SET archived_at=%s,updated_at=%s
        WHERE id=%s AND archived_at IS NULL""",3,batch=True)
    alias("SELECT status,attempts,last_reason FROM sensory_episode_jobs WHERE job_id=?",
        """SELECT status,attempts,last_reason
        FROM amora_pg_v18_lab.sensory_episode_jobs WHERE job_id=%s""",1,1)

    add(EPISODE_FENCE_JOB,"""SELECT selection_json,session_id,correlation_id
        FROM amora_pg_v18_lab.sensory_episode_jobs
        WHERE job_id=%s AND status='claimed' AND lease_token=%s
          AND attempts=%s AND lease_until>%s
        FOR UPDATE""",4,1)
    add(EPISODE_FENCE_EVIDENCE,"""SELECT l.event_id,e.event_type,e.session_id,e.correlation_id,
            i.event_id AS invalidated
        FROM amora_pg_v18_lab.sensory_episode_job_evidence l
        LEFT JOIN amora_pg_v18_lab.events e ON e.id=l.event_id
        LEFT JOIN amora_pg_v18_lab.sensory_episode_invalidations i
          ON i.event_id=l.event_id WHERE l.job_id=%s""",1,4)
    add(EPISODE_FENCE_MEMORY,"""SELECT provenance_source,session_id,metadata_json
        FROM amora_pg_v18_lab.memory_records WHERE id=%s""",1,1)
    add(EPISODE_FENCE_COMPLETE,"""UPDATE amora_pg_v18_lab.sensory_episode_jobs
        SET status='done',available_at=%s,lease_token=NULL,lease_until=NULL,
            last_reason=%s WHERE job_id=%s AND status='claimed'
          AND lease_token=%s AND attempts=%s AND lease_until>%s""",6)
    return r
