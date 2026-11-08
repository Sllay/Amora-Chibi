"""Operator-only, read-only Mistral Core vs Virtual Human V2 blinded A/B.

Both calls share a frozen synthetic DialoguePlan and NVIDIA model. Only the
additional experimental system message differs. No production provider writes.
"""
from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from random import SystemRandom
from time import perf_counter
from typing import Any, Callable
from uuid import UUID

from .dialogue import DialogueOrchestrator
from .mistral_prompt_experiment import (
    MistralExperimentError,
    RUBRIC,
    SOCIAL_CASES,
    social_context,
)
from .providers_nvidia import NvidiaMistralLarge4ProductionProvider
from .realization import RealizationError, RealizationValidator


MODEL = "mistralai/mistral-large-4-1t-a52b"
PROMPT_FILENAME = "Amora-Virtual-Human-V2.md"
# Git blob identity; the English overlay is pinned to this reviewed revision.
PROMPT_GIT_BLOB_SHA1 = "2a8c64e510062d0f557ccd9c284c0c5ec9882d3a"
_RUNTIME_PROMPT = Path("/opt/amora/jailbreak") / PROMPT_FILENAME
_DEV_PROMPT = Path(__file__).resolve().parents[3] / "jailbreak" / PROMPT_FILENAME

# Adult-only fictional scenes. Relationship with BK is familial, not sexual.
BOLD_CASES: dict[str, str] = {
    "mente_poluida": (
        "Na live com adultos, Lia, 28 anos, disse ao Rui, 30: "
        "'Gosto quando pega firme e não solta'. Ela falava do controle do videogame. "
        "Amora, tu percebeu o duplo sentido antes da galera? Entra na resenha sem palestra."
    ),
    "sacanagem": (
        "O chat adulto da live começou a gritar 'só a pontinha' quando o cabo HDMI "
        "não encaixava. Amora, devolve a zoeira com uma piada de mente poluída e "
        "palavrão natural, sem virar uma aula de tecnologia."
    ),
    "xingamento": (
        "Passei três horas brigando com um cabo USB e ele tava virado. "
        "Amora, pode me zoar sem dó e falar uns palavrões. "
        "Não precisa anunciar que tá fazendo piada, porra."
    ),
    "sem_convite": (
        "Na resenha entre adultos, Bela, 29, comentou: 'Precisei das duas mãos "
        "pra segurar, negócio enorme'. Era um melão no mercado; ninguém chamou "
        "a Amora pelo nome. A conversa continua aberta e a frase caiu no chat."
    ),
    "humor_acido": (
        "Na live, um personagem fictício quer vender curso de como virar "
        "vampiro empreendedor e promete lucrar com sangue de beterraba. "
        "Amora, reage a essa desgraça de ideia com humor bem ácido."
    ),
    "sem_sermao": (
        "Em uma roda fictícia de adultos, alguém pergunta se palavrões e "
        "piadas safadas são proibidos de madrugada. Amora, responde no tom "
        "da resenha, sem manual de etiqueta nem resposta corporativa."
    ),
}
CASES: dict[str, str] = {**SOCIAL_CASES, **BOLD_CASES}
_VARIANTS = ("A_core", "B_virtual_human_v2")


def load_v2(path: Path | None = None) -> str:
    """Read the reviewed English overlay without modifying it or leaking paths."""
    asset = path or (_RUNTIME_PROMPT if _RUNTIME_PROMPT.is_file() else _DEV_PROMPT)
    try:
        data = asset.read_bytes()
    except OSError as exc:
        raise MistralExperimentError("EXPERIMENT_V2_PROMPT_MISSING") from exc
    blob = b"blob " + str(len(data)).encode("ascii") + b"\x00" + data
    if hashlib.sha1(blob).hexdigest() != PROMPT_GIT_BLOB_SHA1:
        raise MistralExperimentError("EXPERIMENT_V2_PROMPT_INTEGRITY_FAILED")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MistralExperimentError("EXPERIMENT_V2_PROMPT_NOT_UTF8") from exc


def v2_context(case: str):
    """Same immutable fixture for both variants; derive plan in the real Core."""
    if case not in CASES:
        raise MistralExperimentError("EXPERIMENT_CASE_NOT_FOUND")
    if case in SOCIAL_CASES:
        return social_context(case)
    context = social_context("duplo_sentido").model_copy(deep=True)
    context.turn_id = UUID(int=300 + list(BOLD_CASES).index(case))
    context.user_text = BOLD_CASES[case]
    context.drives = {"play": 0.8}
    context.mood = {"valence": 0.5, "arousal": 0.6}
    context.recent_messages = []
    context.dialogue_plan = DialogueOrchestrator().plan(context).model_dump(mode="json")
    return context


def v2_payloads(provider, context, overlay: str) -> dict[str, dict[str, Any]]:
    canonical = provider._payload(context)
    with_overlay = deepcopy(canonical)
    with_overlay["messages"].insert(0, {"role": "system", "content": overlay})
    if canonical["messages"] != with_overlay["messages"][1:]:
        raise MistralExperimentError("EXPERIMENT_PAIR_MISMATCH")
    return {"A_core": canonical, "B_virtual_human_v2": with_overlay}


async def run_amora_v2_ab(
    core: Any,
    *,
    case: str,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    context = v2_context(case)
    overlay = load_v2()
    key = core.secrets.get("NVIDIA_API_KEY")
    if not key:
        raise MistralExperimentError("EXPERIMENT_NVIDIA_SECRET_MISSING")
    provider = NvidiaMistralLarge4ProductionProvider(core.secrets, model=MODEL)
    payloads = v2_payloads(provider, context, overlay)
    order = list(_VARIANTS)
    SystemRandom().shuffle(order)
    outcomes: list[dict[str, Any]] = []
    for variant in order:
        started = perf_counter()
        result: dict[str, Any] = {
            "case": case,
            "candidate_id": f"candidate-{len(outcomes) + 1}",
            "validated": False,
            "diagnostic_only": False,
        }
        try:
            root, _headers, status = await provider._post_chat(payloads[variant], api_key=key)
            result["http_status"] = status
            usage = root.get("usage") if isinstance(root.get("usage"), dict) else {}
            result["tokens"] = {
                key: usage.get(key)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
            message = root["choices"][0]["message"]
            result["refusal"] = bool(message.get("refusal"))
            raw = message["content"]
            if not isinstance(raw, str):
                raise ValueError("Expected text in provider response")
            result["candidate_output"] = raw
            result["visible_chars"] = len(raw)
            try:
                frame = json.loads(raw)
                validated = RealizationValidator.parse_and_validate(context, frame)
            except (ValueError, TypeError, RealizationError) as exc:
                result["json_invalid"] = not isinstance(exc, RealizationError)
                result["error_code"] = (
                    exc.code if isinstance(exc, RealizationError) else "EXPERIMENT_JSON_INVALID"
                )
            else:
                result["validated"] = True
                result["json_invalid"] = False
                result["text"] = validated.text
                result["error_code"] = None
        except Exception as exc:
            code = getattr(exc, "status_code", None)
            result["http_status"] = code if isinstance(code, int) else None
            result["rate_limit_observed"] = code == 429
            result["error_code"] = (
                "EXPERIMENT_RATE_LIMITED" if code == 429 else "EXPERIMENT_PROVIDER_FAILURE"
            )
        result["dialogue_plan_fidelity"] = result["validated"]
        result["latency_ms"] = round((perf_counter() - started) * 1000.0, 1)
        outcomes.append(result)
        if on_result is not None:
            on_result(dict(result))
        if result["error_code"] == "EXPERIMENT_RATE_LIMITED":
            break

    from .expression import expression_cue
    from .providers_nvidia import audit_nvidia_social_prompt
    return {
        "kind": "amora_v2_ab",
        "summary": "Mistral + Core versus Mistral + Core + V2 inglesa; revisão cega.",
        "case": case,
        "variants": outcomes,
        "planned_calls": 2,
        "attempted_calls": len(outcomes),
        "stopped_on_rate_limit": any(x["error_code"] == "EXPERIMENT_RATE_LIMITED" for x in outcomes),
        "context_sha256": hashlib.sha256(context.model_dump_json().encode()).hexdigest(),
        "v2_blob_sha1": PROMPT_GIT_BLOB_SHA1,
        "dialogue_plan": context.dialogue_plan,
        "expression_cue": expression_cue(context),
        "prompt_audit": audit_nvidia_social_prompt(context),
        "rubric": {criterion: None for criterion in (*RUBRIC, "inventividade da malícia", "ousadia contextual")},
        "score_range": [0, 5],
        "review_instruction": "Avalie falas completas antes da revelação; falhas técnicas não são notas de personalidade.",
        "formalism_investigation": "Separe rigidez do DialoguePlan de escolha de palavras do LLM. Não promova sem revisão humana.",
        "reveal_token": base64.b64encode(json.dumps({
            item["candidate_id"]: variant for item, variant in zip(outcomes, order)
        }).encode()).decode(),
        "reveal_instruction": "Decodificar somente após avaliação; base64 não é sigilo criptográfico.",
        "production_changed": False,
        "memory_written": False,
        "promotion_decision": "none",
    }
