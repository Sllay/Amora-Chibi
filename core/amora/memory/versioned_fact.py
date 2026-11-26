"""Owner-asserted named properties: immutable versions, no inferred slots."""
from __future__ import annotations

import json
import re
import unicodedata
from hashlib import sha256
from amora.cognition.temporal import aware_time

_SLOT = re.compile(r"^fato\s+([\w][\w -]{0,79})\.([\w][\w -]{0,79})\s*=\s*(.{1,320})$",re.I)
_NAMED = re.compile(r"^(?:[oa]\s+)?([\w][\w -]{0,79})\s+d[eo]\s+([\w][\w -]{0,79})\s+[ée]\s+(.{1,320})$",re.I)
_UNCERTAIN = re.compile(r"\b(?:talvez|poderia|pode ser|acho que|sugiro|seria|se fosse|imagino|hip[oó]tese)\b",re.I)
_RELATIVE = {"meu","minha","dele","dela","isso","isto","ele","ela","jogo","projeto"}
_CORRECTION = re.compile(r"^(?:amora\s*,\s*)?(?:(?:na verdade|corrigindo|retificando)\s*[:,]\s*)?",re.I)
_SOURCES = {"cognition:explicit_owner_request","cognition:verified_owner_fact"}


class VersionedFactConflict(RuntimeError):
    """A stale or ambiguous version cannot replace current owner evidence."""


def _normalize(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD",value.casefold())
                            if not unicodedata.combining(c)).split())


def parse_named_fact(content):
    """Only explicit, unambiguous named-property syntax; no model inference."""
    if not isinstance(content,str) or "?" in content or "\n" in content or _UNCERTAIN.search(content):
        return None
    text=_CORRECTION.sub("",content.strip(),count=1).rstrip(".!")
    match=_SLOT.fullmatch(text)
    if match:
        subject,prop,value=match.groups()
    else:
        match=_NAMED.fullmatch(text)
        if not match:
            return None
        prop,subject,value=match.groups()
        if not subject[:1].isupper():
            return None
    # Multiple clauses and quoted/reported speech stay in ordinary reflection.
    if re.search(r"[;\"“”]|\.(?:\s|$)|,\s|\b(?:mas|por[eé]m|entretanto|e|ou)\b",value,re.I):
        return None
    subject,prop=_normalize(subject),_normalize(prop)
    if not subject or not prop or subject in _RELATIVE or prop in _RELATIVE or not value.strip():
        return None
    return sha256((subject+"\x00"+prop).encode()).hexdigest()


def version_metadata(content, observed_at, now):
    key=parse_named_fact(content)
    stamp,current=aware_time(observed_at),aware_time(now)
    if key is None or current is None:
        return {}
    if stamp is None or stamp>current:
        stamp=current
    return {"versioned_fact_key":key,"versioned_fact_at":stamp.isoformat(timespec="microseconds")}


def checked_version_key(record,generation_lease,reflection_lease=None):
    key=record.metadata.get("versioned_fact_key")
    if key is None:
        return None
    source=record.provenance.source
    lease = generation_lease if source=="cognition:explicit_owner_request" else reflection_lease
    marker = "explicit_owner_request" if source=="cognition:explicit_owner_request" else "owner_fact_assertion"
    prefix = "Relato de PERSON_BK: "
    if (not lease or not isinstance(key,str) or not re.fullmatch(r"[0-9a-f]{64}",key)
            or source not in _SOURCES
            or record.provenance.kind.value!="user_assertion" or record.owner_entity_id!="PERSON_BK"
            or record.provenance.actor_id!=record.owner_entity_id
            or record.metadata.get("turn_id")!=lease[0]
            or record.metadata.get(marker) is not True
            or not record.content.startswith(prefix)
            or parse_named_fact(record.content[len(prefix):])!=key
            or aware_time(record.metadata.get("versioned_fact_at")) is None):
        raise VersionedFactConflict("VERSIONED_FACT_AUTHORITY_INVALID")
    return key


def link_version(record,predecessor):
    if predecessor is None:
        return
    raw=predecessor["metadata_json"]
    meta=json.loads(raw) if isinstance(raw,str) else raw
    previous=aware_time(meta.get("versioned_fact_at"))
    current=aware_time(record.metadata.get("versioned_fact_at"))
    if previous is None or current is None or current<=previous:
        raise VersionedFactConflict("VERSIONED_FACT_STALE_OR_AMBIGUOUS")
    record.metadata["supersedes_memory_id"]=str(predecessor["id"])
