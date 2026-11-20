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


# Small semantic states. These are opportunities, not inferred emotions.
OPPORTUNITY_ACTIONS = {
    "question_pending": ("follow_up_question", "light_reaction", "stay_silent"),
    "quick_answer_pending": ("follow_up_question", "light_reaction", "stay_silent"),
    "conversation_quiet": ("continue_topic", "open_contact", "stay_silent"),
    "motivated_contact": ("open_contact", "continue_topic", "stay_silent"),
    "long_absence": ("check_in", "open_contact", "continue_topic", "stay_silent"),
}


def opportunity_state(now, user_at, reply_at, reply_text, *, intention=None):
    """Choose an opportunity from real time and a trusted Core BDI intention.

    The language model does not create delivery authority. A current, strong
    owner-targeted desire may trigger a *chance* to speak earlier than the
    generic quiet-conversation timer; silence remains a valid decision.
    """
    now, user_at, reply_at = map(aware_time, (now, user_at, reply_at))
    if not now or not user_at or not reply_at or not user_at <= reply_at <= now:
        return None
    since_reply = int((now - reply_at).total_seconds())
    since_user = int((now - user_at).total_seconds())
    # Never revive arbitrarily old contact, or send while the user is likely asleep.
    if since_user > 7 * 86400 or not 8 <= now.astimezone(conversation_zone()).hour < 22:
        return None
    text = reply_text.strip().casefold()
    intent_contact = (
        isinstance(intention, dict)
        and intention.get("status") == "active"
        and intention.get("target_id") == "PERSON_BK"
        and (intention.get("kind") == "initiate_contact"
             or (intention.get("kind") == "social_bid"
                 and isinstance(intention.get("metadata"), dict)
                 and intention["metadata"].get("bid_type") in {
                     "seek_contact", "seek_repair", "express_gratitude"
                 }))
        and type(intention.get("selected_utility")) in {float, int}
        and intention["selected_utility"] >= 0.60
    )
    if since_user >= 6 * 3600 and since_reply >= 3600:
        state = "long_absence"
    elif text.endswith("?") and any(x in text for x in ("me responde logo", "responde agora", "me diz logo")) and since_reply >= 120:
        state = "quick_answer_pending"
    elif text.endswith("?") and since_reply >= 5 * 60:
        state = "question_pending"
    elif intent_contact and since_reply >= 8 * 60:
        state = "motivated_contact"
    elif since_reply >= 30 * 60:
        state = "conversation_quiet"
    else:
        return None
    zone = conversation_zone()
    return {
        "state": state, "last_user_at": user_at.astimezone(zone).isoformat(timespec="seconds"),
        "last_amora_at": reply_at.astimezone(zone).isoformat(timespec="seconds"),
        "seconds_since_user": since_user, "seconds_since_amora": since_reply,
        "allowed_actions": list(OPPORTUNITY_ACTIONS[state]),
        "note": "Não houve nova mensagem neste DM desde o último turno. Não presuma motivo, rejeição ou obrigação de responder.",
    }


def trusted_opportunity(raw):
    """Rebuild a small allowlisted Core projection, not literal user content."""
    if not isinstance(raw, dict) or not isinstance(raw.get("state"), str) or raw["state"] not in OPPORTUNITY_ACTIONS:
        return None
    stamps = {key: aware_time(raw.get(key)) for key in ("last_user_at", "last_amora_at")}
    counts = {key: raw.get(key) for key in ("seconds_since_user", "seconds_since_amora")}
    if any(value is None for value in stamps.values()) or any(
        type(value) is not int or not 0 <= value <= 7 * 86400 for value in counts.values()
    ):
        return None
    if stamps["last_user_at"] > stamps["last_amora_at"] or counts["seconds_since_user"] < counts["seconds_since_amora"]:
        return None
    state = raw["state"]
    zone = conversation_zone()
    return {
        "state": state,
        **{key: value.astimezone(zone).isoformat(timespec="seconds") for key, value in stamps.items()},
        **counts, "allowed_actions": list(OPPORTUNITY_ACTIONS[state]),
        "note": "Não houve nova mensagem neste DM desde o último turno. Não presuma motivo, rejeição ou obrigação de responder.",
    }
