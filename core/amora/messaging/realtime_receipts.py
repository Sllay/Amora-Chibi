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
    CHECK ((pending_token IS NULL AND pending_seq IS NULL AND pending_digest IS NULL AND pending_terminal IS NULL)
        OR (pending_token IS NOT NULL AND pending_seq>confirmed_seq AND pending_digest IS NOT NULL AND pending_terminal IS NOT NULL)),
    CHECK (closed=0 OR (pending_token IS NULL AND message_id IS NOT NULL)),
    PRIMARY KEY(turn_id,block_index)
)
"""
SNOWFLAKE = re.compile(r"^[0-9]{17,20}$", re.ASCII)


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


class RealtimeReceiptStore:
    """Lab component; caller must serialize use of each connection.

    SQL uses fixed tables and parameter markers, no externally selected
    identifiers. SQLite writes use BEGIN IMMEDIATE; PostgreSQL uses explicit
    transactions and row locking. Uncertain commits must be reconciled by read.
    """

    def __init__(self, connection, *, postgres=False):
        self.conn = connection
        self.postgres = postgres
        self.table = PG_TABLE if postgres else SQLITE_TABLE

    def install_disposable(self):
        # Explicit test/operator action only. No IF NOT EXISTS adoption.
        self.conn.execute(_DDL.format(table=self.table))
        if not self.postgres:
            self.conn.commit()

    def _sql(self, value):
        # All templates here are static; only the fixed table and parameter
        # notation vary. This lab adapter is not the Core runtime SQL registry.
        value = value.format(table=self.table)
        return value.replace("?", "%s") if self.postgres else value

    def _execute(self, statement, args=()):
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
        return self.conn.transaction() if self.postgres else sqlite_tx()

    def _row(self, turn, index, *, lock=False):
        sql = ("SELECT binding,confirmed_seq,message_id,closed,pending_token,pending_seq,"
               "pending_digest,pending_terminal,confirmed_digest FROM {table} WHERE turn_id=? AND block_index=?")
        if lock and self.postgres:
            sql += " FOR UPDATE"
        row = self._execute(sql,(turn,index)).fetchone()
        if row is None:
            return None
        return dict(zip(("binding","confirmed_seq","message_id","closed","pending_token",
                         "pending_seq","pending_digest","pending_terminal","confirmed_digest"),row))

    @staticmethod
    def _key(turn,index):
        if type(index) is not int or not 0<=index<=31:
            raise ValueError("REALTIME_BLOCK_INDEX_INVALID")
        return str(UUID(str(turn))),index

    def read(self,turn,index):
        return self._row(*self._key(turn,index))

    def prepare(self,turn,index,*,binding,seq,digest,terminal=False):
        turn,index=self._key(turn,index)
        if (type(seq) is not int or seq<1 or type(terminal) is not bool
                or not re.fullmatch(r"[0-9a-f]{64}",binding)
                or not re.fullmatch(r"[0-9a-f]{64}",digest)):
            raise ValueError("REALTIME_PREPARE_INVALID")
        with self._transaction():
            self._execute("INSERT INTO {table}(turn_id,block_index,binding) VALUES(?,?,?) "
                          "ON CONFLICT(turn_id,block_index) DO NOTHING",(turn,index,binding))
            row=self._row(turn,index,lock=True)
            if (row is None or row["binding"]!=binding or row["closed"]
                    or row["pending_token"] is not None or seq<=row["confirmed_seq"]):
                return None
            token=str(uuid4())
            changed=self._execute("UPDATE {table} SET pending_token=?,pending_seq=?,pending_digest=?,"
                                  "pending_terminal=? WHERE turn_id=? AND block_index=? AND binding=? "
                                  "AND pending_token IS NULL AND closed=0 AND confirmed_seq<?",
                                  (token,seq,digest,int(terminal),turn,index,binding,seq)).rowcount
            if changed!=1:
                raise RuntimeError("REALTIME_PREPARE_CONFLICT")
            return Prepared(token,seq,"edit" if row["message_id"] else "create",
                            row["message_id"],digest)

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
            if (row is None or not token or row["binding"]!=binding
                    or row["pending_token"]!=token or row["pending_digest"]!=digest
                    or row["message_id"] not in {None,message_id}):
                return False
            return self._execute("UPDATE {table} SET message_id=?,confirmed_seq=pending_seq,"
                                 "closed=pending_terminal,confirmed_digest=pending_digest,pending_token=NULL,pending_seq=NULL,"
                                 "pending_digest=NULL,pending_terminal=NULL "
                                 "WHERE turn_id=? AND block_index=? AND binding=? AND pending_token=?",
                                 (message_id,turn,index,binding,token)).rowcount==1

    def reject_definitive(self,turn,index,*,binding,token):
        """Release only a proven non-executed operation (e.g. HTTP 429).

        Network errors, timeouts, 5xx and missing receipts are NOT definitive.
        A lost ACK must never be treated as a negative receipt.
        """
        turn,index=self._key(turn,index)
        if not token:
            return False
        with self._transaction():
            return self._execute("UPDATE {table} SET pending_token=NULL,pending_seq=NULL,"
                                 "pending_digest=NULL,pending_terminal=NULL "
                                 "WHERE turn_id=? AND block_index=? AND binding=? AND pending_token=?",
                                 (turn,index,binding,token)).rowcount==1
