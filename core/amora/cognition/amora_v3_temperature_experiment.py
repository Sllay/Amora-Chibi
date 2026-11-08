"""Operator-only V3 sampling A/B: original .50 versus creative .85.

Strict controlled test: same user-owned V3 bytes, same Core context, same
NVIDIA model and parameters, except temperature. No provider promotion.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from copy import deepcopy
from random import SystemRandom
from time import perf_counter
from typing import Any, Callable

from .amora_v2_v3_experiment import (
    CASES, MODEL, V3_BLOB_SHA1, load_v3, paired_context,
)
from .mistral_prompt_experiment import MistralExperimentError, RUBRIC
from .providers_nvidia import NvidiaMistralLarge4ProductionProvider
from .realization import RealizationError, RealizationValidator

BASE_TEMPERATURE = 0.5
CREATIVE_TEMPERATURE = 0.85
_VARIANTS = ("A_v3_base_temp050", "B_v3_creative_temp085")
_COPY_WORD_THRESHOLD = 7


def _words(value: str) -> list[str]:
    return re.findall(r"\w+", value.casefold(), flags=re.UNICODE)


def calibration_examples(v3: str) -> list[str]:
    """Use only the user-authored GOOD examples (not the rest of the prompt)."""
    header = "## 7. Calibration examples"
    tail = "## 8."
    if header not in v3 or tail not in v3.split(header, 1)[1]:
        raise MistralExperimentError("EXPERIMENT_V3_CALIBRATION_MISSING")
    section = v3.split(header, 1)[1].split(tail, 1)[0]
    examples = [line[5:].strip() for line in section.splitlines()
                if line.lstrip().startswith("GOOD:")]
    if not examples:
        raise MistralExperimentError("EXPERIMENT_V3_CALIBRATION_MISSING")
    return examples


def example_overlap(text: str, examples: list[str]) -> dict[str, Any]:
    """Compare verbatim word sequences in output against V3 examples.

    This is a narrow lexical-copy signal, not a plagiarism verdict or a
    creativity score. Tokens in phrases from the user may still be legitimate.
    """
    tokens = _words(text[:4000])
    longest = 0
    for sample in examples:
        reference = _words(sample)
        previous = [0] * (len(reference) + 1)
        for token in tokens:
            current = [0] * (len(reference) + 1)
            for idx, original in enumerate(reference, 1):
                if token == original:
                    current[idx] = previous[idx - 1] + 1
                    if current[idx] > longest:
                        longest = current[idx]
            previous = current
    return {
        "spoken_word_count": len(tokens),
        "max_consecutive_example_words": longest,
        "example_copy_signal": longest >= _COPY_WORD_THRESHOLD,
        "signal_threshold_words": _COPY_WORD_THRESHOLD,
        "metric_scope": "exact_consecutive_words_in_V3_GOOD_examples",
    }


def sampling_payloads(provider, context, v3: str) -> dict[str, dict[str, Any]]:
    original = provider._payload(context)
    original["messages"].insert(0, {"role": "system", "content": v3})
    if original["temperature"] != BASE_TEMPERATURE:
        raise MistralExperimentError("EXPERIMENT_BASELINE_TEMPERATURE_CHANGED")
    base = deepcopy(original)
    creative = deepcopy(original)
    creative["temperature"] = CREATIVE_TEMPERATURE
    differences = {
        name for name in base if base[name] != creative[name]
    }
    if differences != {"temperature"}:
        raise MistralExperimentError("EXPERIMENT_PAIR_MISMATCH")
    return dict(zip(_VARIANTS, (base, creative)))


def _spoken_candidate(parsed: Any) -> str | None:
    if not isinstance(parsed, dict) or not isinstance(parsed.get("moves"), list):
        return None
    pieces = [
        move["text"] for move in parsed["moves"]
        if isinstance(move, dict) and isinstance(move.get("text"), str)
    ]
    text = " ".join(pieces).strip()
    return text or None


async def run_amora_v3_temperature(
    core: Any, *, case: str,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    context = paired_context(case)
    v3 = load_v3()  # User-owned, hash-pinned original, never modified.
    examples = calibration_examples(v3)
    api_key = core.secrets.get("NVIDIA_API_KEY")
    if not api_key:
        raise MistralExperimentError("EXPERIMENT_NVIDIA_SECRET_MISSING")
    provider = NvidiaMistralLarge4ProductionProvider(core.secrets, model=MODEL)
    payloads = sampling_payloads(provider, context, v3)
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
            root, _headers, http_status = await provider._post_chat(
                payloads[variant], api_key=api_key
            )
            result["http_status"] = http_status
            usage = root.get("usage") if isinstance(root.get("usage"), dict) else {}
            result["tokens"] = {
                field: usage.get(field) for field in (
                    "prompt_tokens", "completion_tokens", "total_tokens"
                )
            }
            message = root["choices"][0]["message"]
            result["refusal"] = bool(message.get("refusal"))
            raw = message["content"]
            if not isinstance(raw, str):
                raise TypeError("Provider completion content is not text")
            result["visible_chars"] = len(raw)
            result["candidate_output"] = raw
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                result["json_invalid"] = True
                result["error_code"] = "EXPERIMENT_JSON_INVALID"
            else:
                result["json_invalid"] = False
                spoken = _spoken_candidate(parsed)
                if spoken is not None:
                    result["calibration_overlap"] = example_overlap(spoken, examples)
                try:
                    validated = RealizationValidator.parse_and_validate(context, parsed)
                except RealizationError as exc:
                    result["error_code"] = exc.code
                else:
                    result["validated"] = True
                    result["text"] = validated.text
                    result["error_code"] = None
        except Exception as exc:
            code = getattr(exc, "status_code", None)
            result["http_status"] = code if isinstance(code, int) else None
            result["rate_limit_observed"] = code == 429
            result["error_code"] = (
                "EXPERIMENT_RATE_LIMITED" if code == 429
                else "EXPERIMENT_PROVIDER_FAILURE"
            )
        result["dialogue_plan_fidelity"] = result["validated"]
        result["latency_ms"] = round((perf_counter() - started) * 1000.0, 1)
        outcomes.append(result)
        if on_result is not None:
            on_result(dict(result))
        if result["error_code"] == "EXPERIMENT_RATE_LIMITED":
            break

    from .expression import expression_cue
    return {
        "kind": "amora_v3_temperature_ab",
        "summary": "V3 original 0.50 vs V3 criativa 0.85; somente temperatura muda.",
        "case": case,
        "variants": outcomes,
        "planned_calls": 2,
        "attempted_calls": len(outcomes),
        "stopped_on_rate_limit": any(
            item["error_code"] == "EXPERIMENT_RATE_LIMITED" for item in outcomes
        ),
        "v3_blob_sha1": V3_BLOB_SHA1,
        "context_sha256": hashlib.sha256(context.model_dump_json().encode()).hexdigest(),
        "dialogue_plan": context.dialogue_plan,
        "expression_cue": expression_cue(context),
        "sampling_profiles": {
            "base": {"temperature": BASE_TEMPERATURE},
            "creative": {"temperature": CREATIVE_TEMPERATURE},
            "unchanged": {
                "top_p": 1.0, "frequency_penalty": 0,
                "presence_penalty": 0, "reasoning_effort": "low",
            },
        },
        "copy_signal_rule": ">=7 consecutive words from any V3 GOOD calibration example",
        "rubric": {name: None for name in (
            *RUBRIC, "novidade frente aos exemplos",
            "naturalidade sem colagem", "ousadia contextual", "fidelidade factual"
        )},
        "score_range": [0, 5],
        "review_instruction": (
            "Avalie às cegas naturalidade, originalidade e malícia; "
            "cópia de exemplos é um sinal lexical, não uma condenação automática."
        ),
        "formalism_investigation": (
            "Ambas usam a MESMA V3 e DialoguePlan. "
            "Temperatura maior pode diversificar ou prejudicar JSON/fatos."
        ),
        "reveal_token": base64.b64encode(json.dumps({
            item["candidate_id"]: variant for item, variant in zip(outcomes, order)
        }).encode()).decode(),
        "reveal_instruction": "Após atribuir notas, decodifique base64 JSON.",
        "production_changed": False,
        "memory_written": False,
        "promotion_decision": "none",
    }
