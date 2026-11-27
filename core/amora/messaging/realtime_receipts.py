"""Candidate Realtime receipt protocol (explicit disposable schema only).

Persist operational IDs and transition state, never draft text, prompt, or
reasoning. The embedding controller must independently authorize each action
and reconcile canonical outbox delivery. A pending POST is not replayable.
No runtime schema registration, migration, API exposure or automatic activation.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from uuid import UUID, uuid4

# Exact identical schema shape in disposable SQLite and PG18 connections.
# Caller supplies a dedicated, already-pinned lab connection.
SQLITE_TABLE = "realtime_receipts"
PG_TABLE = "amora_pg_v18_lab.realtime_receipts"
_DDL = """
CREATE TABLE {table} (
    turn_id TEXT NOT NULL,
    block_index INTEGER NOT NULL CHECK(block_index BETWEEN 0 AND 31),
    binding TEXT NOT NULL,
    confirmed_seq INTEGER NOT NULL DEFAULT 0 CHECK(confirmed_seq>=0),
    message_id TEXT UNIQUE,
    confirmed_digest TEXT,
    closed INTEGER NOT NULL DEFAULT 0 CHECK(closed IN (0,1)),
    pending_token TEXT,
    pending_seq INTEGER,
    pending_digest TEXT,
    pending_terminal INTEGER CHECK(pending_terminal IN (0,1)),
    account_scope TEXT,
    channel_id TEXT,
    pending_nonce TEXT UNIQUE,
    confirmed_token TEXT,
    CHECK ((pending_token IS NULL AND pending_seq IS NULL AND pending_digest IS NULL AND pending_terminal IS NULL)
        OR (pending_token IS NOT NULL AND pending_seq>confirmed_seq AND pending_digest IS NOT NULL AND pending_terminal IS NOT NULL)),
    CHECK (closed=0 OR (pending_token IS NULL AND message_id IS NOT NULL)),
    PRIMARY KEY(turn_id,block_index)
)
"""
SNOWFLAKE = re.compile(r"^[0-9]{17,20}$", re.ASCII)

ORPHAN_TEXT = "Geração interrompida."
ORPHAN_DIGEST = sha256(ORPHAN_TEXT.encode("utf-8")).hexdigest()
_ORPHAN_COLUMNS = ("turn_id","binding","channel_id","message_id","confirmed_seq",
                   "pending_token","pending_seq","pending_digest","pending_nonce","pending_terminal")
_ORPHAN_BASE = (
    "SELECT r.turn_id,r.binding,r.channel_id,r.message_id,r.confirmed_seq,"
    "r.pending_token,r.pending_seq,r.pending_digest,r.pending_nonce,r.pending_terminal "
    "FROM {table} r WHERE r.account_scope=? AND r.block_index=0 AND r.closed=0 "
    "AND r.channel_id IS NOT NULL "
    "AND (r.pending_token IS NOT NULL OR (r.message_id IS NOT NULL "
    "AND r.confirmed_digest<>?)) "
    "AND NOT EXISTS (SELECT 1 FROM messaging_reply_jobs j WHERE j.turn_id=r.turn_id "
    "AND ( (j.status='running' AND j.lease_until>?) OR "
    "(j.status='ready' AND EXISTS (SELECT 1 FROM messaging_outbound_queue o "
    "WHERE o.id=j.outbound_id AND o.status IN ('queued','claimed'))) )) "
)
_ORPHAN_LIST = _ORPHAN_BASE + "AND r.turn_id>? ORDER BY r.turn_id LIMIT 8"
_ORPHAN_ONE = _ORPHAN_BASE + "AND r.turn_id=? LIMIT 1"


def binding_digest(account: str, channel: str, source: str, revision: int) -> str:
    if (not isinstance(revision,int) or isinstance(revision,bool) or not 1<=revision<=1_000_000
            or not account or not SNOWFLAKE.fullmatch(channel) or not SNOWFLAKE.fullmatch(source)):
        raise ValueError("REALTIME_BINDING_INVALID")
    import json
    return sha256(json.dumps([account,channel,source,revision],ensure_ascii=True,
                             separators=(",",":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Prepared:
    token: str
    seq: int
    operation: str
    message_id: str | None
    digest: str
    nonce: str | None = None


def receipt_nonce(token: str) -> str:
    return "r1"+sha256(token.encode("ascii")).hexdigest()[:22]


class RealtimeReceiptStore:
    """Lab component; caller must serialize use of each connection.

    SQL uses fixed tables and parameter markers, no externally selected
    identifiers. SQLite writes use BEGIN IMMEDIATE; PostgreSQL uses explicit
    transactions and row locking. Uncertain commits must be reconciled by read.
    """

    def __init__(self, connection, *, postgres=False, reviewed=False):
        self.conn = connection
        self.postgres = postgres
        self.reviewed = reviewed
        self.table = PG_TABLE if postgres else SQLITE_TABLE

    def install_disposable(self):
        # Explicit test/operator action only. No IF NOT EXISTS adoption.
        if self.reviewed:
            raise ValueError("REALTIME_MIGRATION_REQUIRES_OPERATOR")
        self.conn.execute(_DDL.format(table=self.table))
        if not self.postgres:
            self.conn.commit()

    def _sql(self, value):
        # All templates here are static; only the fixed table and parameter
        # notation vary. This lab adapter is not the Core runtime SQL registry.
        value = value.format(table=self.table)
        if self.postgres:
            value = value.replace("FROM messaging_reply_jobs ", "FROM amora_pg_v18_lab.messaging_reply_jobs ")
            value = value.replace("FROM messaging_outbound_queue ", "FROM amora_pg_v18_lab.messaging_outbound_queue ")
            return value.replace("?", "%s")
        return value

    def _execute(self, statement, args=()):
        if self.reviewed and self.postgres:
            from amora.storage.postgres_runtime_realtime import RECEIPT_KEYS
            key=RECEIPT_KEYS.get(statement)
            if key is None:
                raise ValueError("REALTIME_SQL_NOT_REVIEWED")
            return self.conn.execute(key,args)
        return self.conn.execute(self._sql(statement), args)

    def _transaction(self):
        from contextlib import contextmanager
        @contextmanager
        def sqlite_tx():
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.conn.rollback()
                raise
            else:
                self.conn.commit()
        if self.reviewed:
            # Same owned connection and transaction boundary as reply jobs.
            return self.conn
        return self.conn.transaction() if self.postgres else sqlite_tx()

    def _row(self, turn, index, *, lock=False):
        sql = ("SELECT binding,confirmed_seq,message_id,closed,pending_token,pending_seq,"
               "pending_digest,pending_terminal,confirmed_digest,account_scope,channel_id,pending_nonce,confirmed_token "
               "FROM {table} WHERE turn_id=? AND block_index=?")
        if lock and self.postgres:
            sql += " FOR UPDATE"
        row = self._execute(sql,(turn,index)).fetchone()
        if row is None:
            return None
        return dict(zip(("binding","confirmed_seq","message_id","closed","pending_token",
                         "pending_seq","pending_digest","pending_terminal","confirmed_digest",
                         "account_scope","channel_id","pending_nonce","confirmed_token"),
                        (row[i] for i in range(13))))

    @staticmethod
    def _key(turn,index):
        if type(index) is not int or not 0<=index<=31:
            raise ValueError("REALTIME_BLOCK_INDEX_INVALID")
        return str(UUID(str(turn))),index

    def read(self,turn,index):
        return self._row(*self._key(turn,index))

    def orphan_candidates(self,account,now,after=""):
        rows=self._execute(_ORPHAN_LIST,(account,ORPHAN_DIGEST,now,after)).fetchall()
        return [dict(zip(_ORPHAN_COLUMNS,(row[i] for i in range(len(_ORPHAN_COLUMNS))))) for row in rows]

    def orphan_read(self,turn,account,now):
        row=self._execute(_ORPHAN_ONE,(account,ORPHAN_DIGEST,now,str(UUID(str(turn))))).fetchone()
        return (dict(zip(_ORPHAN_COLUMNS,(row[i] for i in range(len(_ORPHAN_COLUMNS)))))
                if row is not None else None)

    def prepare(self,turn,index,*,binding,seq,digest,terminal=False,account=None,channel=None,
                require_edit=False):
        turn,index=self._key(turn,index)
        if (type(seq) is not int or seq<1 or type(terminal) is not bool
                or not re.fullmatch(r"[0-9a-f]{64}",binding)
                or not re.fullmatch(r"[0-9a-f]{64}",digest)):
            raise ValueError("REALTIME_PREPARE_INVALID")
        with self._transaction():
            if (account is None)!=(channel is None) or (account is not None and
                    (not isinstance(account,str) or not 1<=len(account)<=96
                     or not isinstance(channel,str) or not SNOWFLAKE.fullmatch(channel))):
                raise ValueError("REALTIME_DESTINATION_INVALID")
            self._execute("INSERT INTO {table}(turn_id,block_index,binding,account_scope,channel_id) VALUES(?,?,?,?,?) "
                          "ON CONFLICT(turn_id,block_index) DO NOTHING",(turn,index,binding,account,channel))
            row=self._row(turn,index,lock=True)
            if (row is None or row["binding"]!=binding or row["closed"]
                    or row["account_scope"]!=account or row["channel_id"]!=channel
                    or row["pending_token"] is not None or seq<=row["confirmed_seq"]):
                return None
            if require_edit and not row["message_id"]:
                return None
            token=str(uuid4())
            nonce=None if row["message_id"] else receipt_nonce(token)
            changed=self._execute("UPDATE {table} SET pending_token=?,pending_seq=?,pending_digest=?,"
                                  "pending_terminal=?,pending_nonce=? WHERE turn_id=? AND block_index=? AND binding=? "
                                  "AND pending_token IS NULL AND closed=0 AND confirmed_seq<?",
                                  (token,seq,digest,int(terminal),nonce,turn,index,binding,seq)).rowcount
            if changed!=1:
                raise RuntimeError("REALTIME_PREPARE_CONFLICT")
            return Prepared(token,seq,"edit" if row["message_id"] else "create",
                            row["message_id"],digest,nonce)

    def confirm(self,turn,index,*,binding,token,message_id,digest):
        """Only call with independently verified physical Discord receipt.

        A caller may reconcile an uncertain operation with such a receipt.
        An existing receipt cannot be changed to a different message ID.
        """
        turn,index=self._key(turn,index)
        if not isinstance(message_id,str) or not SNOWFLAKE.fullmatch(message_id):
            return False
        with self._transaction():
            row=self._row(turn,index,lock=True)
            if (row is not None and token and row["binding"]==binding
                    and row["confirmed_token"]==token and row["confirmed_digest"]==digest
                    and row["message_id"]==message_id):
                return True
            if (row is None or not token or row["binding"]!=binding
                    or row["pending_token"]!=token or row["pending_digest"]!=digest
                    or row["message_id"] not in {None,message_id}):
                return False
            return self._execute("UPDATE {table} SET message_id=?,confirmed_seq=pending_seq,"
                                 "closed=pending_terminal,confirmed_digest=pending_digest,confirmed_token=pending_token,"
                                 "pending_nonce=NULL,pending_token=NULL,pending_seq=NULL,"
                                 "pending_digest=NULL,pending_terminal=NULL "
                                 "WHERE turn_id=? AND block_index=? AND binding=? AND pending_token=?",
                                 (message_id,turn,index,binding,token)).rowcount==1

    def observe_create(self,*,nonce,account,channel,message_id,digest):
        """Positive receipt only; absence or similar text is never evidence."""
        if (not isinstance(nonce,str) or not re.fullmatch(r"r1[0-9a-f]{22}",nonce)
                or not isinstance(channel,str) or not SNOWFLAKE.fullmatch(channel)
                or not isinstance(message_id,str) or not SNOWFLAKE.fullmatch(message_id)):
            return False
        row=self._execute("SELECT turn_id,block_index,binding,pending_token FROM {table} "
                          "WHERE pending_nonce=? AND account_scope=? AND channel_id=? "
                          "AND pending_digest=? AND message_id IS NULL",
                          (nonce,account,channel,digest)).fetchone()
        if row is None or receipt_nonce(row[3])!=nonce:
            return False
        return self.confirm(row[0],row[1],binding=row[2],token=row[3],message_id=message_id,digest=digest)

    def reject_definitive(self,turn,index,*,binding,token):
        """Release only a proven non-executed operation (e.g. HTTP 429).

        Network errors, timeouts, 5xx and missing receipts are NOT definitive.
        A lost ACK must never be treated as a negative receipt.
        """
        turn,index=self._key(turn,index)
        if not token:
            return False
        with self._transaction():
            return self._execute("UPDATE {table} SET pending_nonce=NULL,pending_token=NULL,pending_seq=NULL,"
                                 "pending_digest=NULL,pending_terminal=NULL "
                                 "WHERE turn_id=? AND block_index=? AND binding=? AND pending_token=?",
                                 (turn,index,binding,token)).rowcount==1
