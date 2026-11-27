"""Fixed, separately approved PG18 Realtime receipt SQL. No dynamic SQL fallback."""
from .postgres_runtime_connection import ReviewedStatement

_SELECT=("SELECT binding,confirmed_seq,message_id,closed,pending_token,pending_seq,"
         "pending_digest,pending_terminal,confirmed_digest,account_scope,channel_id,pending_nonce,confirmed_token "
         "FROM {table} WHERE turn_id=? AND block_index=?")
_INSERT=("INSERT INTO {table}(turn_id,block_index,binding,account_scope,channel_id) VALUES(?,?,?,?,?) "
         "ON CONFLICT(turn_id,block_index) DO NOTHING")
_PREPARE=("UPDATE {table} SET pending_token=?,pending_seq=?,pending_digest=?,"
          "pending_terminal=?,pending_nonce=? WHERE turn_id=? AND block_index=? AND binding=? "
          "AND pending_token IS NULL AND closed=0 AND confirmed_seq<?")
_CONFIRM=("UPDATE {table} SET message_id=?,confirmed_seq=pending_seq,"
          "closed=pending_terminal,confirmed_digest=pending_digest,confirmed_token=pending_token,"
          "pending_nonce=NULL,pending_token=NULL,pending_seq=NULL,"
          "pending_digest=NULL,pending_terminal=NULL "
          "WHERE turn_id=? AND block_index=? AND binding=? AND pending_token=?")
_REJECT=("UPDATE {table} SET pending_nonce=NULL,pending_token=NULL,pending_seq=NULL,"
         "pending_digest=NULL,pending_terminal=NULL "
         "WHERE turn_id=? AND block_index=? AND binding=? AND pending_token=?")
_OBSERVE=("SELECT turn_id,block_index,binding,pending_token FROM {table} "
          "WHERE pending_nonce=? AND account_scope=? AND channel_id=? "
          "AND pending_digest=? AND message_id IS NULL")
RECEIPT_KEYS={_OBSERVE:"PG_REALTIME_OBSERVE",_SELECT:"PG_REALTIME_READ",_SELECT+" FOR UPDATE":"PG_REALTIME_LOCK",
              _INSERT:"PG_REALTIME_INSERT",_PREPARE:"PG_REALTIME_PREPARE",
              _CONFIRM:"PG_REALTIME_CONFIRM",_REJECT:"PG_REALTIME_REJECT"}


def realtime_statements():
    select=("SELECT binding,confirmed_seq,message_id,closed,pending_token,pending_seq,"
            "pending_digest,pending_terminal,confirmed_digest,account_scope,channel_id,pending_nonce,confirmed_token "
            "FROM amora_pg_v18_lab.realtime_receipts WHERE turn_id=%s AND block_index=%s")
    return {
        "PG_REALTIME_READ":ReviewedStatement(select,2,1),
        "PG_REALTIME_LOCK":ReviewedStatement(select+" FOR UPDATE",2,1),
        "PG_REALTIME_INSERT":ReviewedStatement(
            "INSERT INTO amora_pg_v18_lab.realtime_receipts(turn_id,block_index,binding,account_scope,channel_id) VALUES(%s,%s,%s,%s,%s) "
            "ON CONFLICT(turn_id,block_index) DO NOTHING",5),
        "PG_REALTIME_PREPARE":ReviewedStatement(
            "UPDATE amora_pg_v18_lab.realtime_receipts SET pending_token=%s,pending_seq=%s,pending_digest=%s,"
            "pending_terminal=%s,pending_nonce=%s WHERE turn_id=%s AND block_index=%s AND binding=%s "
            "AND pending_token IS NULL AND closed=0 AND confirmed_seq<%s",9),
        "PG_REALTIME_CONFIRM":ReviewedStatement(
            "UPDATE amora_pg_v18_lab.realtime_receipts SET message_id=%s,confirmed_seq=pending_seq,"
            "closed=pending_terminal,confirmed_digest=pending_digest,confirmed_token=pending_token,"
          "pending_nonce=NULL,pending_token=NULL,pending_seq=NULL,"
            "pending_digest=NULL,pending_terminal=NULL "
            "WHERE turn_id=%s AND block_index=%s AND binding=%s AND pending_token=%s",5),
        "PG_REALTIME_OBSERVE":ReviewedStatement(
            "SELECT turn_id,block_index,binding,pending_token FROM amora_pg_v18_lab.realtime_receipts "
            "WHERE pending_nonce=%s AND account_scope=%s AND channel_id=%s "
            "AND pending_digest=%s AND message_id IS NULL",4,1),
        "PG_REALTIME_REJECT":ReviewedStatement(
            "UPDATE amora_pg_v18_lab.realtime_receipts SET pending_nonce=NULL,pending_token=NULL,pending_seq=NULL,"
            "pending_digest=NULL,pending_terminal=NULL "
            "WHERE turn_id=%s AND block_index=%s AND binding=%s AND pending_token=%s",4),
    }
