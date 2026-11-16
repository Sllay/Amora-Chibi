"""Conservative Portuguese agenda commands. No autonomous LLM scheduling."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, time
import re
import unicodedata
from zoneinfo import ZoneInfo

from .models import ZONE


@dataclass(frozen=True)
class AgendaIntent:
    kind: str
    content: str = ""
    due_at: datetime | None = None
    interval_minutes: int | None = None
    until_at: datetime | None = None
    year: int | None = None


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def parse_agenda_text(message: str, now: datetime):
    raw = message.strip()
    folded = _fold(raw)
    if not raw or len(raw) > 1000 or now.tzinfo is None:
        return None
    zone = ZoneInfo(ZONE)
    local = now.astimezone(zone)
    # Optional vocative only; do not interpret arbitrary reported speech.
    if folded.startswith("amora"):
        folded = re.sub(r"^amora[,!\s]*", "", folded).strip()
        raw = re.sub(r"^amora[,!\s]*", "", raw, flags=re.IGNORECASE).strip()
    if re.search(r"\b(cancela|cancelar|apaga|apagar|remove|remover|nao precisa avisar)\b", folded) and (
        "agenda" in folded or "lembrete" in folded
    ):
        year = re.search(r"\b(20\d{2}|21\d{2})\b", folded)
        query = re.sub(r"\b(cancela|cancelar|apaga|apagar|remove|remover|agenda|lembrete|da|do|de|a|o|nao|precisa|avisar|em|ano|aquela|aquele)\b", " ", folded)
        query = re.sub(r"\b(20\d{2}|21\d{2})\b", " ", query).strip()
        return AgendaIntent("cancel_search", year=int(year.group(0)) if year else None, content=query)
    if re.search(r"\b(quais|listar|lista|mostra|mostrar|tenho)\b", folded) and (
        "agenda" in folded or "lembrete" in folded
    ):
        year = re.search(r"\b(20\d{2}|21\d{2})\b", folded)
        return AgendaIntent("list", year=int(year.group(0)) if year else None)
    repeat = re.search(r"\ba cada\s+(\d{1,4})\s+minuto(?:s)?\b", folded)
    if repeat:
        if not any(x in folded for x in ("fala", "falar", "diz", "dizer", "avisa", "avisar", "lembra", "lembrar")):
            return None
        minutes = int(repeat.group(1))
        if minutes < 10 or minutes > 10080:
            return AgendaIntent("invalid_interval")
        quoted = re.search(r"[\"“‘'](.+?)[\"”’']", raw)
        if quoted:
            content = quoted.group(1).strip()
        else:
            content = re.split(r"\b(?:dizendo|falando|com a mensagem|a frase)\b", raw, maxsplit=1, flags=re.IGNORECASE)
            content = content[-1].strip() if len(content) > 1 else ""
        if not content:
            return AgendaIntent("need_content")
        if "dia todo" not in folded and "ate" not in folded:
            return AgendaIntent("need_end")
        end = datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)
        due = local + timedelta(minutes=minutes)
        if due >= end:
            return AgendaIntent("no_time_left_today")
        return AgendaIntent("create", content=content, due_at=due,
                            interval_minutes=minutes, until_at=end)
    intent = re.search(r"\b(me lembra|lembra pra mim|me avisa|me lembra de|agenda|anota pra)\b", folded)
    date = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", folded)
    if not intent and not (date and folded.startswith("em ")):
        return None
    clock = re.search(r"\b(?:as|a)?\s*(\d{1,2})[:h](\d{2})\b", folded)
    if not clock:
        return AgendaIntent("need_time")
    hour, minute = map(int, clock.groups())
    if hour > 23 or minute > 59:
        return AgendaIntent("need_time")
    if date:
        day, month = int(date.group(1)), int(date.group(2))
        year = int(date.group(3)) if date.group(3) else local.year
        try:
            due = datetime(year, month, day, hour, minute, tzinfo=zone)
            if not date.group(3) and due <= local:
                due = due.replace(year=year + 1)
        except ValueError:
            return AgendaIntent("need_valid_date")
    elif "amanha" in folded:
        due = datetime.combine(local.date() + timedelta(days=1), time(hour, minute), tzinfo=zone)
    elif "hoje" in folded:
        due = datetime.combine(local.date(), time(hour, minute), tzinfo=zone)
    else:
        due = datetime.combine(local.date(), time(hour, minute), tzinfo=zone)
        if due <= local:
            due += timedelta(days=1)
    rest = raw[clock.end():]
    rest = re.sub(r"^\s*(?:de|pra|para|que|sobre|me)\s+", "", rest, flags=re.IGNORECASE).strip(" .,:;")
    if not rest:
        return AgendaIntent("need_content")
    return AgendaIntent("create", content=rest, due_at=due)
