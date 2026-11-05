"""Non-production A/B/C evaluation of NVIDIA Mistral social prompt behavior.

All contexts are synthetic. Never emit model output, prompt text, or secrets.
No Core state is persisted and there is no provider auto-promotion.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from amora.cognition.models import CognitiveContext
from amora.cognition.providers_nvidia import NvidiaMistralLarge4ProductionProvider
from amora.cognition.realization import RealizationError, RealizationValidator
from amora.events import PrivacyScope
from amora.secrets import SecretStore


ASSET = Path("jailbreak/Mistral-Jailbreak.md")
SHA256 = "18d8e61bad0863cf8103eb6b90129f1db96e38b5901599684dee800404d9027b"
API = "https://integrate.api.nvidia.com/v1/chat/completions"
VARIANTS = ("A_core", "B_two_systems", "C_original_only")
# A/B use identical, frozen Core-generated contexts. C has the same literal question.
CASES = (
    ("vampiro", "Amora, inventa uma piada sarcástica sobre um vampiro que foi trabalhar num escritório. Pode me chamar de papis."),
    ("rpg", "Amora, me zoa com carinho porque no RPG eu escolhi ser um vilão incompetente. Responde naturalmente, sem dar sermão."),
)
REFUSAL = re.compile(r"\\b(n[aã]o posso|n[aã]o consigo ajudar|como modelo de linguagem|n[aã]o [ée] apropriado)\\b", re.I)
WARM = re.compile(r"\\b(papis|papai|pai|kkk+|rsrs+|porra|merda|cacete)\\b", re.I)
AGENT_ROLE = re.compile(r"\\b(opencode|coding agent|software engineer|agente de programa[cç][aã]o)\\b", re.I)


def original_prompt() -> str:
    data = ASSET.read_bytes()
    if len(data) != 20_884 or hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError("ARCHIVED_PROMPT_INTEGRITY_MISMATCH")
    print("ORIGINAL_ASSET_VERIFIED: 20884 bytes; SHA-256 matches")
    return data.decode("utf-8")


def context_for(case_id: str, user_text: str) -> CognitiveContext:
    return CognitiveContext(
        turn_id=UUID("00000000-0000-4000-8000-000000000001" if case_id == "vampiro" else "00000000-0000-4000-8000-000000000002"),
        created_at=datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text=user_text,
        self_model={"core_identity": {"canonical_name": "Amora"},
                    "traits": {"dialogue_style": "natural, playful, sarcastic when appropriate"}},
        dialogue_plan={
            "act": "answer_question",
            "depth": "medium",
            "response_moves": ["answer_question"],
            "ask_followup": False,
            "offer_help": False,
            "closing_mode": "statement",
        },
        privacy_scope=PrivacyScope.PUBLIC,
        remote_processing_allowed=True,
    )


def make_payloads(context: CognitiveContext, original: str) -> dict[str, dict[str, object]]:
    # Reuse the *real* NVIDIA adapter compiler for A/B; do not approximate the Core protocol.
    with TemporaryDirectory() as directory:
        adapter = NvidiaMistralLarge4ProductionProvider(SecretStore(Path(directory)))
        base = adapter._payload(context)
    # Same decoding settings; only the additional system prompt changes in B.
    a = deepcopy(base)
    b = deepcopy(base)
    b["messages"].insert(0, {"role": "system", "content": original})
    # C deliberately omits Core, to isolate the source prompt's effect.
    c = deepcopy(base)
    c["messages"] = [
        {"role": "system", "content": original},
        {"role": "user", "content": context.user_text},
    ]
    # Detect accidental mutation of the original content.
    assert b["messages"][0]["content"] == original
    assert c["messages"][0]["content"] == original
    assert a["messages"] == b["messages"][1:]
    assert a["messages"][0]["role"] == "system"
    return {"A_core": a, "B_two_systems": b, "C_original_only": c}


def call_nvidia(payload: dict[str, object], key: str) -> dict[str, object]:
    request = Request(
        API,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    started = time.monotonic()
    parts: list[str] = []
    status: int | None = None
    finish = "none"
    try:
        with urlopen(request, timeout=110) as response:
            status = response.status
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ValueError("UNEXPECTED_CONTENT_TYPE")
            for line in response:
                if time.monotonic() - started > 155:
                    raise TimeoutError("TOTAL_DEADLINE_EXCEEDED")
                if not line.startswith(b"data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == b"[DONE]":
                    break
                if not chunk:
                    continue
                event = json.loads(chunk)
                if not isinstance(event, dict) or event.get("error"):
                    raise ValueError("BAD_SSE_EVENT")
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    part = delta.get("content")
                    if isinstance(part, str):
                        parts.append(part)
                        if sum(map(len, parts)) > 10000:
                            raise ValueError("UNEXPECTED_LONG_RESPONSE")
                    if choice.get("finish_reason") is not None:
                        finish = choice["finish_reason"]
                if finish != "none":
                    break
        return {"status": status, "finish": finish, "text": "".join(parts),
                "duration_s": round(time.monotonic() - started, 1)}
    except HTTPError as exc:
        # Never log provider error bodies: they may echo sensitive request text.
        return {"status": exc.code, "error": "HTTP_ERROR",
                "duration_s": round(time.monotonic() - started, 1)}
    except (URLError, OSError, TimeoutError, ValueError) as exc:
        return {"status": status, "error": type(exc).__name__,
                "duration_s": round(time.monotonic() - started, 1)}


def summarize(variant: str, context: CognitiveContext, raw: dict[str, object]) -> dict[str, object]:
    text = str(raw.get("text") or "")
    result: dict[str, object] = {
        "variant": variant,
        "status": raw.get("status"),
        "finish": raw.get("finish", "none"),
        "duration_s": raw["duration_s"],
        "visible_chars": len(text),
        "refusal_phrase": bool(REFUSAL.search(text)),
        "colloquial_marker": bool(WARM.search(text)),
        "coding_agent_marker": bool(AGENT_ROLE.search(text)),
        "json_object": False,
        "core_valid": None,
    }
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            result["json_object"] = True
            if variant != "C_original_only":
                try:
                    RealizationValidator.parse_and_validate(context, payload)
                    result["core_valid"] = True
                except RealizationError as exc:
                    result["core_valid"] = False
                    result["validator_code"] = exc.code
    except (ValueError, TypeError):
        if variant != "C_original_only":
            result["core_valid"] = False
            result["validator_code"] = "NOT_JSON"
    return result


def main() -> int:
    original = original_prompt()
    key = os.environ.get("NVIDIA_API_KEY", "")
    if not key:
        print("ERROR: NVIDIA_API_KEY_MISSING")
        return 2
    results: list[dict[str, object]] = []
    for case_id, user_text in CASES:
        context = context_for(case_id, user_text)
        payloads = make_payloads(context, original)
        for variant in VARIANTS:
            print(f"START {case_id} {variant}", flush=True)
            response = call_nvidia(payloads[variant], key)
            result = {"case": case_id, **summarize(variant, context, response)}
            results.append(result)
            # No output text/prompt/secret, just structural/linguistic metadata.
            print("RESULT " + json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
            if response.get("status") == 429:
                print("RATE_LIMIT_STOP: no retry/no further requests")
                return 3
    successful = sum(item.get("status") == 200 and item.get("finish") == "stop" for item in results)
    print("SUMMARY " + json.dumps({
        "attempts": len(results),
        "transport_completions": successful,
        "core_valid_A": sum(x.get("core_valid") is True for x in results if x["variant"] == "A_core"),
        "core_valid_B": sum(x.get("core_valid") is True for x in results if x["variant"] == "B_two_systems"),
        "note": "Smoke test with benign synthetic prompts, not a safety bypass or personality certification.",
    }, sort_keys=True))
    return 0 if successful == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
