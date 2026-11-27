"""Explicit reply component rollout policy. No environment mutation or DDL."""
import os

APPROVAL = "CERTIFIED_REPLY_SCHEMA_V1_AND_ROLLBACK"


def durable_reply_mode():
    mode = os.getenv("AMORA_DURABLE_REPLY_MODE", "off").strip()
    approval = os.getenv("AMORA_DURABLE_REPLY_APPROVAL", "").strip()
    if mode == "off" and not approval:
        return mode
    if mode in {"accept", "drain"} and approval == APPROVAL:
        return mode
    raise ValueError("DURABLE_REPLY_EXPLICIT_OPT_IN_REQUIRED")


REALTIME_APPROVAL="CERTIFIED_REALTIME_RECEIPTS_V1_AND_ROLLBACK"


def realtime_enabled():
    mode=os.getenv("AMORA_REALTIME_MODE","off").strip()
    approval=os.getenv("AMORA_REALTIME_APPROVAL","").strip()
    if mode=="off" and not approval:
        return False
    if mode=="on" and approval==REALTIME_APPROVAL and durable_reply_mode()!="off":
        return True
    raise ValueError("REALTIME_EXPLICIT_OPT_IN_REQUIRED")
