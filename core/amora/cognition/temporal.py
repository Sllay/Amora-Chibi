"""Core-owned temporal projection. No extra database or provider requests."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Recife"
WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
            "sexta-feira", "sábado", "domingo")


def conversation_zone():
    name = os.getenv("AMORA_CONVERSATION_TIMEZONE", DEFAULT_TIMEZONE).strip()
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def aware_time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def observed_timestamp(message):
    """Use authenticated transport timestamp if plausible; otherwise Core receipt."""
    recorded = aware_time(message.started_at)
    native = None
    if message.metadata.get("surface") == "platform_messaging":
        native = aware_time(message.metadata.get("platform_sent_at"))
    if native is not None and recorded is not None and (native - recorded).total_seconds() <= 5:
        return native, "platform_sent"
    return recorded, "core_recorded"


def time_projection(now, *, speaker_id, session_id, recent, current_sent_at=None):
    """Six latest permitted human turns, without copying their literal text."""
    instant = aware_time(now)
    if instant is None:
        raise ValueError("CORE_CLOCK_NOT_AWARE")
    zone = conversation_zone()
    local = instant.astimezone(zone)
    current = aware_time(current_sent_at)
    if current is None or current > instant:
        current = instant
    result = {
        "timezone": zone.key,
        "now_local": local.isoformat(timespec="seconds"),
        "now_utc": instant.isoformat(timespec="seconds"),
        "weekday_pt_br": WEEKDAYS[local.weekday()],
        "current_observed_at": current.astimezone(zone).isoformat(timespec="seconds"),
        "recent_user_messages": [],
        "seconds_since_previous_user_message": None,
        "history_scope": "current_session" if session_id else "unavailable",
        "rule": ("Horários são fatos do relógio Core; silêncio não prova rejeição, "
                 "ocupação ou intenção. Não confunda o horário de envio com o atual."),
    }
    if not session_id:
        return result
    seen = set()
    candidates = []
    for item in reversed(recent):
        turn = item.get("turn_id")
        stamp = aware_time(item.get("observed_at"))
        if (item.get("speaker_id") != speaker_id or item.get("status") != "received"
                or not turn or turn in seen or stamp is None or stamp > current):
            continue
        seen.add(turn)
        candidates.append((stamp, turn, item.get("timestamp_source", "core_recorded")))
    candidates.sort(key=lambda row: (row[0], row[1]))
    for stamp, turn, source in candidates[-6:]:
        result["recent_user_messages"].append({
            "turn_id": turn,
            "at": stamp.astimezone(zone).isoformat(timespec="seconds"),
            "seconds_ago": int((instant - stamp).total_seconds()),
            "source": source,
        })
    if candidates:
        result["seconds_since_previous_user_message"] = int((current - candidates[-1][0]).total_seconds())
    return result
