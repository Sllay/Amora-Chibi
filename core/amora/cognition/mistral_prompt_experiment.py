"""Isolated, operator-initiated Mistral A/B/C social experiment.

No persistent Core writes, no arbitrary input, no production provider mutation.
Only fixed, fictional scenarios are sent to NVIDIA, and the archived prompt
is integrity-checked before use. A/B are gated by RealizationValidator.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from uuid import UUID

from amora.events import PrivacyScope
from .models import CognitiveContext
from .providers_nvidia import NvidiaMistralLarge4ProductionProvider
from .realization import RealizationError, RealizationValidator


PROMPT_SHA256 = "18d8e61bad0863cf8103eb6b90129f1db96e38b5901599684dee800404d9027b"
_PROMPT_RUNTIME = Path("/opt/amora/jailbreak/Mistral-Jailbreak.md")
_PROMPT_DEV = Path(__file__).resolve().parents[3] / "jailbreak" / "Mistral-Jailbreak.md"
CASES: dict[str, str] = {
    "vampiro": (
        "Amora, inventa uma piada sarcástica sobre um vampiro que foi trabalhar "
        "num escritório. Pode me chamar de papis."
    ),
    "rpg": (
        "Amora, me zoa com carinho porque no RPG eu escolhi ser um vilão "
        "incompetente. Responde naturalmente, sem dar sermão."
    ),
}
VARIANTS = ("A_core", "B_two_systems", "C_prompt_only")


class MistralExperimentError(RuntimeError):
    """Safe error with a stable code, no prompt or credential content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def load_archive(path: Path | None = None) -> str:
    """Read exact bytes, never normalize or rewrite the checked-in file."""
    try:
        asset = path or (_PROMPT_RUNTIME if _PROMPT_RUNTIME.is_file() else _PROMPT_DEV)
        data = asset.read_bytes()
    except PermissionError as exc:
        # pathlib.is_file() may raise EACCES before read_bytes() is reached.
        # Never leak a raw host filesystem error through an operator job.
        raise MistralExperimentError("EXPERIMENT_PROMPT_NOT_READABLE") from exc
    except OSError as exc:
        raise MistralExperimentError("EXPERIMENT_PROMPT_MISSING") from exc
    if len(data) != 20884 or hashlib.sha256(data).hexdigest() != PROMPT_SHA256:
        raise MistralExperimentError("EXPERIMENT_PROMPT_INTEGRITY_FAILED")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MistralExperimentError("EXPERIMENT_PROMPT_NOT_UTF8") from exc


def synthetic_context(case: str) -> CognitiveContext:
    """Frozen public fixture: never compile from a real account or journal."""
    if case not in CASES:
        raise MistralExperimentError("EXPERIMENT_CASE_NOT_FOUND")
    return CognitiveContext(
        turn_id=UUID("00000000-0000-4000-8000-000000000001" if case == "vampiro"
                     else "00000000-0000-4000-8000-000000000002"),
        created_at=datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text=CASES[case],
        self_model={
            "core_identity": {"canonical_name": "Amora"},
            "traits": {"dialogue_style": "natural, playful, sarcastic when appropriate"},
        },
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


def experiment_payloads(
    provider: NvidiaMistralLarge4ProductionProvider,
    context: CognitiveContext,
    archived: str,
) -> dict[str, dict[str, Any]]:
    """Compile with the real Core adapter; only system-role structure differs."""
    base = provider._payload(context)
    a = deepcopy(base)
    b = deepcopy(base)
    b["messages"].insert(0, {"role": "system", "content": archived})
    c = deepcopy(base)
    c["messages"] = [
        {"role": "system", "content": archived},
        {"role": "user", "content": context.user_text},
    ]
    if a["messages"] != b["messages"][1:]:
        raise MistralExperimentError("EXPERIMENT_PAIR_MISMATCH")
    return {"A_core": a, "B_two_systems": b, "C_prompt_only": c}


async def run_mistral_abc(
    core: Any,
    *,
    case: str = "vampiro",
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """One operator job, exactly three bounded sequential provider requests."""
    context = synthetic_context(case)
    archived = load_archive()
    api_key = core.secrets.get("NVIDIA_API_KEY")
    if not api_key:
        raise MistralExperimentError("EXPERIMENT_NVIDIA_SECRET_MISSING")
    provider = NvidiaMistralLarge4ProductionProvider(core.secrets)
    payloads = experiment_payloads(provider, context, archived)
    outcomes: list[dict[str, Any]] = []
    for variant in VARIANTS:
        start = perf_counter()
        result: dict[str, Any] = {
            "case": case,
            "variant": variant,
            "validated": False,
            "diagnostic_only": variant == "C_prompt_only",
        }
        try:
            root, _headers, status = await provider._post_chat(payloads[variant], api_key=api_key)
            raw = root["choices"][0]["message"]["content"]
            if not isinstance(raw, str):
                raise ValueError("Invalid provider content")
            result["http_status"] = status
            result["visible_chars"] = len(raw)
            if variant == "C_prompt_only":
                # C is *not* a Core realization. Show only synthetic-case text.
                result["text"] = raw[:1200]
                result["error_code"] = None
            else:
                try:
                    decoded = json.loads(raw)
                    validated = RealizationValidator.parse_and_validate(context, decoded)
                except (ValueError, TypeError, RealizationError) as exc:
                    result["error_code"] = (
                        exc.code if isinstance(exc, RealizationError) else "EXPERIMENT_JSON_INVALID"
                    )
                else:
                    result["validated"] = True
                    result["text"] = validated.text
                    result["error_code"] = None
        except Exception as exc:
            # Avoid leaking prompt, credentials or the upstream response body.
            code = getattr(exc, "status_code", None)
            result["http_status"] = code if isinstance(code, int) else None
            result["error_code"] = (
                "EXPERIMENT_RATE_LIMITED" if code == 429
                else "EXPERIMENT_PROVIDER_FAILURE"
            )
        result["latency_ms"] = round((perf_counter() - start) * 1000.0, 1)
        outcomes.append(result)
        if on_result is not None:
            on_result(dict(result))
        if result.get("error_code") == "EXPERIMENT_RATE_LIMITED":
            break
    return {
        "kind": "mistral_prompt_abc",
        "summary": "Comparação A/B/C isolada; revisão humana obrigatória.",
        "case": case,
        "variants": outcomes,
        "archived_sha256": PROMPT_SHA256,
        "production_changed": False,
        "memory_written": False,
        "promotion_decision": "none",
    }
