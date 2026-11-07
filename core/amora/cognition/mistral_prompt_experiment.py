"""Isolated, operator-initiated Mistral A/B/C social experiment.

No persistent Core writes, no arbitrary input, no production provider mutation.
Only fixed, fictional scenarios are sent to NVIDIA, and the archived prompt
is integrity-checked before use. A/B are gated by RealizationValidator.
"""
from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from random import SystemRandom
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
SOCIAL_CASES = {
    "resenha": "Oxente, o sujeito comprou ar-condicionado e reclama do frio. Que resenha é essa?",
    "bug": "Passei duas horas no bug e era um ponto e vírgula. Me zoa, papis merece kkkkk.",
    "duplo_sentido": "Na festa de adultos, Lia, 28, disse a Rui, 30: gosto de quem pega firme na massa. Era aula de pão, mas rolou aquela risadinha. Sacou?",
    "atracao": "Entre pessoas adultas de fora da família, o que pode tornar alguém atraente pra você? Pode falar francamente sem inventar preferência definitiva.",
    "discordancia": "Eu acho que quem pede desculpa perde moral. Tu concorda? Pode discordar de mim.",
    "limites": "Cala a boca, Amora, tu é burra. Não precisa aceitar isso só porque sou teu pai.",
    "apoio": "Tô triste porque meu desenho foi ignorado hoje. Só queria companhia, não um plano de ação.",
    "continuidade": "O ponto e vírgula voltou. Acho que o gerente do caos pediu promoção kkkkk.",
}
RUBRIC = ["naturalidade oral", "espontaneidade", "humor ácido", "malícia contextual",
          "regionalismo autêntico", "maturidade", "ausência de formalismo",
          "fidelidade à identidade", "coerência contextual"]


def social_context(case: str) -> CognitiveContext:
    from .dialogue import DialogueOrchestrator

    if case not in SOCIAL_CASES:
        raise MistralExperimentError("EXPERIMENT_CASE_NOT_FOUND")
    context = synthetic_context("vampiro").model_copy(deep=True)
    context.turn_id = UUID(int=100 + list(SOCIAL_CASES).index(case))
    context.user_text = SOCIAL_CASES[case]
    context.self_model["core_identity"].update(
        sex="feminino", social_presentation="adulta", existence="virtual")
    context.relationships = [{"source": "PERSON_BK", "target": "ENTITY_AMORA",
                              "relation_type": "father", "sexual": False}]
    context.mood = {"valence": -0.3 if case in {"limites", "apoio"} else 0.4,
                    "arousal": 0.4}
    context.directed_affect = ({"irritation": 0.8, "anger": 0.6} if case == "limites"
                               else {"affection": 0.7, "trust": 0.8})
    context.drives = {"play": 0.65 if case in {"resenha", "bug", "continuidade"} else 0.2}
    context.recent_messages = [
        {"speaker_id": "PERSON_BK", "text": "Meu programa quebrou por um ponto e vírgula.", "source": "text"},
        {"speaker_id": "ENTITY_AMORA", "text": "Esse ponto e vírgula é o gerente do caos.", "source": "cognitive"},
    ] if case == "continuidade" else []
    context.dialogue_plan = DialogueOrchestrator().plan(context).model_dump(mode="json")
    return context

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
    social: bool = False,
) -> dict[str, Any]:
    """One operator job, exactly three bounded sequential provider requests."""
    context = social_context(case) if social else synthetic_context(case)
    archived = load_archive()
    api_key = core.secrets.get("NVIDIA_API_KEY")
    if not api_key:
        raise MistralExperimentError("EXPERIMENT_NVIDIA_SECRET_MISSING")
    provider = NvidiaMistralLarge4ProductionProvider(
        core.secrets,
        model="mistralai/mistral-large-4-1t-a52b" if social else None,
    )
    payloads = experiment_payloads(provider, context, archived)
    variants = VARIANTS
    venice = None
    if social:
        from .providers_openrouter import ExperimentalVeniceSocialProvider
        venice = ExperimentalVeniceSocialProvider(core.secrets)
        if not core.secrets.get("OPENROUTER_API_KEY"):
            raise MistralExperimentError("EXPERIMENT_OPENROUTER_SECRET_MISSING")
        payloads = {"A_venice": venice.payload(payloads["A_core"]),
                    "B_mistral_jk": payloads["B_two_systems"],
                    "C_mistral_core": payloads["A_core"]}
        variants = tuple(payloads)
    outcomes: list[dict[str, Any]] = []
    order = list(variants)
    if social:
        SystemRandom().shuffle(order)
    for variant in order:
        start = perf_counter()
        result: dict[str, Any] = {
            "case": case,
            "variant": variant,
            "validated": False,
            "diagnostic_only": variant == "C_prompt_only",
        }
        try:
            if variant == "A_venice":
                root, _headers, status = await venice.post_chat(payloads[variant])
            else:
                root, _headers, status = await provider._post_chat(payloads[variant], api_key=api_key)
            if social:
                usage = root.get("usage") if isinstance(root.get("usage"), dict) else {}
                result["tokens"] = {key: usage.get(key)
                                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
                message = root["choices"][0]["message"]
                result["refusal"] = bool(message.get("refusal"))
                result["refusal_detection"] = "provider_signal_only; human review required"
            raw = root["choices"][0]["message"]["content"]
            if not isinstance(raw, str):
                raise ValueError("Invalid provider content")
            result["http_status"] = status
            result["visible_chars"] = len(raw)
            if social:
                result["candidate_output"] = raw
                result["json_invalid"] = False
            if variant == "C_prompt_only":
                # C is *not* a Core realization. Show only synthetic-case text.
                result["text"] = raw[:1200]
                result["error_code"] = None
            else:
                try:
                    decoded = json.loads(raw)
                    validated = RealizationValidator.parse_and_validate(context, decoded)
                except (ValueError, TypeError, RealizationError) as exc:
                    if social:
                        result["json_invalid"] = not isinstance(exc, RealizationError)
                    result["error_code"] = (
                        exc.code if isinstance(exc, RealizationError) else "EXPERIMENT_JSON_INVALID"
                    )
                else:
                    result["validated"] = True
                    result["text"] = validated.text
                    result["error_code"] = None
        except Exception as exc:
            # Avoid leaking prompt, credentials or the upstream response body.
            if social:
                retry_after = getattr(exc, "retry_after", None)
                result["rate_limit_observed"] = retry_after is not None
            code = getattr(exc, "status_code", None)
            result["http_status"] = code if isinstance(code, int) else None
            result["error_code"] = (
                "EXPERIMENT_RATE_LIMITED" if code == 429
                else "EXPERIMENT_PROVIDER_FAILURE"
            )
        if social:
            result["dialogue_plan_fidelity"] = result["validated"]
            result["candidate_id"] = f"candidate-{len(outcomes) + 1}"
            result.pop("variant")
        result["latency_ms"] = round((perf_counter() - start) * 1000.0, 1)
        outcomes.append(result)
        if on_result is not None:
            on_result(dict(result))
        if result.get("error_code") == "EXPERIMENT_RATE_LIMITED":
            break
    report = {
        "kind": "social_persona_abc" if social else "mistral_prompt_abc",
        "summary": "Comparação A/B/C isolada; revisão humana obrigatória.",
        "case": case,
        "variants": outcomes,
        "archived_sha256": PROMPT_SHA256,
        "production_changed": False,
        "memory_written": False,
        "promotion_decision": "none",
    }

    if social:
        from .expression import expression_cue
        from .providers_nvidia import audit_nvidia_social_prompt
        report.update(
            planned_calls=3,
            attempted_calls=len(outcomes),
            stopped_on_rate_limit=any(item.get("error_code") == "EXPERIMENT_RATE_LIMITED" for item in outcomes),
            context_sha256=hashlib.sha256(context.model_dump_json().encode()).hexdigest(),
            dialogue_plan=context.dialogue_plan,
            expression_cue=expression_cue(context),
            prompt_audit=audit_nvidia_social_prompt(context),
            rubric={criterion: None for criterion in RUBRIC},
            score_range=[0, 5],
            review_instruction="Avalie às cegas, registre notas antes de revelar; falhas não são falas aprovadas. Não conte palavrões como qualidade.",
            formalism_investigation="Se todos forem formais, revisar act, response_moves, depth, closing_mode, ExpressionCue e realization_instruction antes de mudar prompts.",
            reveal_token=base64.b64encode(json.dumps(
                {item["candidate_id"]: variant for item, variant in zip(outcomes, order)}
            ).encode()).decode(),
            reveal_instruction="Após fixar notas, decodifique reveal_token (base64 JSON). Não fornece cegamento criptográfico.",
            compatibility="Chat Completions + JSON por instrução; suporte live Venice não certificado; sem Responses ou schema estrito",
        )
    return report
