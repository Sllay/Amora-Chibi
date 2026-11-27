"""Fixed, separately approved PG18 Realtime receipt SQL. No dynamic SQL fallback."""
from .postgres_runtime_connection import ReviewedStatement

_SELECT=("SELECT binding,confirmed_seq,message_id,closed,pending_token,pending_seq,"
         "pending_digest,pending_terminal,confirmed_digest FROM {table} WHERE turn_id=? AND block_index=?")
_INSERT=("INSERT INTO {table}(turn_id,block_index,binding) VALUES(?,?,?) "
         "ON CONFLICT(turn_id,block_index) DO NOTHING")
_PREPARE=("UPDATE {table} SET pending_token=?,pending_seq=?,pending_digest=?,"
          "pending_terminal=? WHERE turn_id=? AND block_index=? AND binding=? "
          "AND pending_token IS NULL AND closed=0 AND confirmed_seq<?")
_CONFIRM=("UPDATE {table} SET message_id=?,confirmed_seq=pending_seq,"
          "closed=pending_terminal,confirmed_digest=pending_digest,pending_token=NULL,pending_seq=NULL,"
          "pending_digest=NULL,pending_terminal=NULL "
          "WHERE turn_id=? AND block_index=? AND binding=? AND pending_token=?")
_REJECT=("UPDATE {table} SET pending_token=NULL,pending_seq=NULL,"
         "pending_digest=NULL,pending_terminal=NULL "
         "WHERE turn_id=? AND block_index=? AND binding=? AND pending_token=?")
RECEIPT_KEYS={_SELECT:"PG_REALTIME_READ",_SELECT+" FOR UPDATE":"PG_REALTIME_LOCK",
              _INSERT:"PG_REALTIME_INSERT",_PREPARE:"PG_REALTIME_PREPARE",
              _CONFIRM:"PG_REALTIME_CONFIRM",_REJECT:"PG_REALTIME_REJECT"}


def realtime_statements():
    select=("SELECT binding,confirmed_seq,message_id,closed,pending_token,pending_seq,"
            "pending_digest,pending_terminal,confirmed_digest "
            "FROM amora_pg_v18_lab.realtime_receipts WHERE turn_id=%s AND block_index=%s")
    return {
        "PG_REALTIME_READ":ReviewedStatement(select,2,1),
        "PG_REALTIME_LOCK":ReviewedStatement(select+" FOR UPDATE",2,1),
        "PG_REALTIME_INSERT":ReviewedStatement(
            "INSERT INTO amora_pg_v18_lab.realtime_receipts(turn_id,block_index,binding) VALUES(%s,%s,%s) "
            "ON CONFLICT(turn_id,block_index) DO NOTHING",3),
        "PG_REALTIME_PREPARE":ReviewedStatement(
            "UPDATE amora_pg_v18_lab.realtime_receipts SET pending_token=%s,pending_seq=%s,pending_digest=%s,"
            "pending_terminal=%s WHERE turn_id=%s AND block_index=%s AND binding=%s "
            "AND pending_token IS NULL AND closed=0 AND confirmed_seq<%s",8),
        "PG_REALTIME_CONFIRM":ReviewedStatement(
            "UPDATE amora_pg_v18_lab.realtime_receipts SET message_id=%s,confirmed_seq=pending_seq,"
            "closed=pending_terminal,confirmed_digest=pending_digest,pending_token=NULL,pending_seq=NULL,"
            "pending_digest=NULL,pending_terminal=NULL "
            "WHERE turn_id=%s AND block_index=%s AND binding=%s AND pending_token=%s",5),
        "PG_REALTIME_REJECT":ReviewedStatement(
            "UPDATE amora_pg_v18_lab.realtime_receipts SET pending_token=NULL,pending_seq=NULL,"
            "pending_digest=NULL,pending_terminal=NULL "
            "WHERE turn_id=%s AND block_index=%s AND binding=%s AND pending_token=%s",4),
    }
