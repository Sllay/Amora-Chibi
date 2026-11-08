"""Operator-only V3 sampling A/B with operator-selected temperature/top_p.

Strict controlled test: same user-owned V3 bytes, same Core context, same
NVIDIA model and prompt, except explicitly selected sampling values. No promotion.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import dataclass
from copy import deepcopy
from random import SystemRandom
from time import perf_counter
from typing import Any, Callable

from .amora_v2_v3_experiment import (
    CASES, MODEL, V3_BLOB_SHA1, load_v3, paired_context,
)
from .mistral_prompt_experiment import MistralExperimentError, RUBRIC
from .providers_nvidia import NvidiaMistralLarge4ProductionProvider
from .amora_experiment_transport import (
    classify_provider_failure, experiment_provider,
    EXPERIMENT_IDLE_SECONDS, EXPERIMENT_MAX_TOTAL_SECONDS,
)
from .realization import RealizationError, RealizationValidator

BASE_TEMPERATURE = 0.5
CREATIVE_TEMPERATURE = 0.75
_VARIANTS = ("A_v3_control", "B_v3_test")
_COPY_WORD_THRESHOLD = 7


@dataclass(frozen=True, slots=True)
class SamplingProfile:
    temperature: float
    top_p: float

    def __post_init__(self) -> None:
        if (not math.isfinite(self.temperature) or
                not (0.0 <= self.temperature <= 2.0)):
            raise ValueError("temperature must be finite and between 0 and 2")
        if not math.isfinite(self.top_p) or not (0.0 < self.top_p <= 1.0):
            raise ValueError("top_p must be finite and greater than 0, at most 1")

    def as_dict(self) -> dict[str, float]:
        return {"temperature": self.temperature, "top_p": self.top_p}


DEFAULT_CONTROL = SamplingProfile(BASE_TEMPERATURE, 1.0)
DEFAULT_TEST = SamplingProfile(CREATIVE_TEMPERATURE, 1.0)

_FLAGS = {
    "--case": "case",
    "--temperature": "test_temperature",
    "--top-p": "test_top_p",
    "--temperature-a": "control_temperature",
    "--top-p-a": "control_top_p",
    "--temperature-b": "test_temperature",
    "--top-p-b": "test_top_p",
}


def _numeric_parameter(raw: str) -> float:
    # Accept 0.75 or 0,75; reject scientific notation, NaN, inf and injections.
    normalized = raw.replace(",", ".")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,4})?", normalized):
        raise ValueError("invalid numeric sampling value")
    return float(normalized)


def parse_sampling_args(args: list[str]) -> tuple[str, SamplingProfile, SamplingProfile]:
    """Closed, order-independent command grammar. No freeform provider fields."""
    if not args or len(args) % 2:
        raise ValueError("expected flag/value pairs")
    parsed: dict[str, str] = {}
    for flag, raw in zip(args[0::2], args[1::2]):
        field = _FLAGS.get(flag)
        if field is None or field in parsed:
            raise ValueError("unknown or repeated sampling argument")
        parsed[field] = raw
    case = parsed.get("case")
    if not case or case not in CASES:
        raise ValueError("unknown or missing case")
    control = SamplingProfile(
        temperature=_numeric_parameter(parsed["control_temperature"])
        if "control_temperature" in parsed else DEFAULT_CONTROL.temperature,
        top_p=_numeric_parameter(parsed["control_top_p"])
        if "control_top_p" in parsed else DEFAULT_CONTROL.top_p,
    )
    test = SamplingProfile(
        temperature=_numeric_parameter(parsed["test_temperature"])
        if "test_temperature" in parsed else DEFAULT_TEST.temperature,
        top_p=_numeric_parameter(parsed["test_top_p"])
        if "test_top_p" in parsed else DEFAULT_TEST.top_p,
    )
    if control == test:
        raise ValueError("sampling profiles are identical; change at least one value")
    return case, control, test


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


def sampling_payloads(
    provider, context, v3: str, *,
    control: SamplingProfile = DEFAULT_CONTROL,
    test: SamplingProfile = DEFAULT_TEST,
) -> dict[str, dict[str, Any]]:
    original = provider._payload(context)
    original["messages"].insert(0, {"role": "system", "content": v3})
    if (original["temperature"], original["top_p"]) != (
        BASE_TEMPERATURE, 1.0
    ):
        raise MistralExperimentError("EXPERIMENT_BASELINE_SAMPLING_CHANGED")
    if control == test:
        raise MistralExperimentError("EXPERIMENT_SAMPLING_PROFILES_IDENTICAL")
    a, b = deepcopy(original), deepcopy(original)
    for payload, profile in ((a, control), (b, test)):
        payload["temperature"] = profile.temperature
        payload["top_p"] = profile.top_p
    differences = {name for name in a if a[name] != b[name]}
    expected = {
        field for field in ("temperature", "top_p") if a[field] != b[field]
    }
    if differences != expected or not expected:
        raise MistralExperimentError("EXPERIMENT_PAIR_MISMATCH")
    return dict(zip(_VARIANTS, (a, b)))

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
    control: SamplingProfile = DEFAULT_CONTROL,
    test: SamplingProfile = DEFAULT_TEST,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    context = paired_context(case)
    v3 = load_v3()  # User-owned, hash-pinned original, never modified.
    examples = calibration_examples(v3)
    api_key = core.secrets.get("NVIDIA_API_KEY")
    if not api_key:
        raise MistralExperimentError("EXPERIMENT_NVIDIA_SECRET_MISSING")
    provider = experiment_provider(core.secrets, model=MODEL)
    payloads = sampling_payloads(provider, context, v3, control=control, test=test)
    order = list(_VARIANTS)
    SystemRandom().shuffle(order)
    outcomes: list[dict[str, Any]] = []
    for variant in order:
        started = perf_counter()
        result: dict[str, Any] = {
            "case": case,
            "candidate_id": f"candidate-{len(outcomes) + 1}",
            "validated": False,
            "validation_status": "not_run",
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
                result["validation_status"] = "invalid_model_json"
                result["error_code"] = "EXPERIMENT_JSON_INVALID"
            else:
                result["json_invalid"] = False
                spoken = _spoken_candidate(parsed)
                if spoken is not None:
                    result["calibration_overlap"] = example_overlap(spoken, examples)
                try:
                    validated = RealizationValidator.parse_and_validate(context, parsed)
                except RealizationError as exc:
                    result["validation_status"] = "rejected_by_core"
                    result["error_code"] = exc.code
                else:
                    result["validated"] = True
                    result["validation_status"] = "passed"
                    result["text"] = validated.text
                    result["error_code"] = None
        except Exception as exc:
            # Safe stage-aware diagnostics, no upstream body or stack trace.
            classify_provider_failure(result, exc)
        result["dialogue_plan_fidelity"] = (
            result["validated"] if result["validation_status"] in {
                "passed", "rejected_by_core",
            } else None
        )
        result["latency_ms"] = round((perf_counter() - started) * 1000.0, 1)
        outcomes.append(result)
        if on_result is not None:
            on_result(dict(result))
        if result["error_code"] == "EXPERIMENT_RATE_LIMITED":
            break

    from .expression import expression_cue
    return {
        "kind": "amora_v3_temperature_ab",
        "summary": "V3 original com dois perfis de sampling definidos pelo operador.",
        "case": case,
        "variants": outcomes,
        "transport_deadlines": {
            "idle_seconds": EXPERIMENT_IDLE_SECONDS,
            "max_total_seconds": EXPERIMENT_MAX_TOTAL_SECONDS,
            "production_unchanged": True,
        },
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
            "control": control.as_dict(),
            "test": test.as_dict(),
            "unchanged": {
                "frequency_penalty": 0,
                "presence_penalty": 0, "reasoning_effort": "low",
            },
        },
        "changed_parameters": [
            name for name in ("temperature", "top_p")
            if control.as_dict()[name] != test.as_dict()[name]
        ],
        "sampling_comparison_note": (
            "Ambos temperature e top_p foram alterados: não é possível atribuir "
            "diferenças de comportamento a somente um parâmetro."
            if control.temperature != test.temperature and control.top_p != test.top_p
            else "Apenas um parâmetro de sampling mudou entre os perfis."
        ),
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
            "Amostragem mais variada pode diversificar ou prejudicar JSON/fatos."
        ),
        "reveal_token": base64.b64encode(json.dumps({
            item["candidate_id"]: variant for item, variant in zip(outcomes, order)
        }).encode()).decode(),
        "reveal_instruction": "Após atribuir notas, decodifique base64 JSON.",
        "production_changed": False,
        "memory_written": False,
        "promotion_decision": "none",
    }
