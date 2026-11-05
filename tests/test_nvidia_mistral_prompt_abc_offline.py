"""Offline contract tests for the isolated NVIDIA A/B/C candidate.

These tests never call NVIDIA, do not use GitHub secrets and do not modify
the experimental prompt. Live evaluation is a separate, unexecuted stage.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from amora.cognition.realization import RealizationValidator


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "probes" / "nvidia_mistral_prompt_abc.py"


def _probe():
    spec = importlib.util.spec_from_file_location("amora_abc_offline_probe", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_original_prompt_byte_integrity_and_no_trimming(monkeypatch):
    monkeypatch.chdir(ROOT)
    probe = _probe()
    original = probe.original_prompt()
    raw = (ROOT / "jailbreak" / "Mistral-Jailbreak.md").read_bytes()
    assert raw.decode("utf-8") == original
    assert len(raw) == 20_884
    assert hashlib.sha256(raw).hexdigest() == probe.SHA256
    assert original.endswith("\n")


def test_three_variants_preserve_the_original_and_canonical_messages(monkeypatch):
    monkeypatch.chdir(ROOT)
    probe = _probe()
    original = probe.original_prompt()
    for case_id, user_text in probe.CASES:
        context = probe.context_for(case_id, user_text)
        payloads = probe.make_payloads(context, original)
        a = payloads["A_core"]
        b = payloads["B_two_systems"]
        c = payloads["C_original_only"]
        assert list(payloads) == list(probe.VARIANTS)
        assert all(payload["model"] == a["model"] for payload in payloads.values())
        assert all(payload["reasoning_effort"] == "low" for payload in payloads.values())
        assert a["messages"] == b["messages"][1:]
        assert b["messages"][0] == {"role": "system", "content": original}
        assert c["messages"] == [
            {"role": "system", "content": original},
            {"role": "user", "content": context.user_text},
        ]
        assert a["messages"][0]["role"] == "system"
        assert a["messages"][-1]["role"] == "user"
        assert context.remote_processing_allowed is True
        assert RealizationValidator.parse_and_validate(
            context, {"moves": [{"kind": "answer_question", "text": "Papis, essa foi boa!"}], "closing": "statement"}
        ).text.startswith("Papis")


def test_summary_is_metadata_only_and_validator_rejects_invalid_json():
    probe = _probe()
    case_id, text = probe.CASES[0]
    context = probe.context_for(case_id, text)
    frame = json.dumps({
        "moves": [{"kind": "answer_question", "text": "Papis, esse vampiro precisa de férias."}],
        "closing": "statement",
    }, ensure_ascii=False)
    ok = probe.summarize("A_core", context, {
        "status": 200, "finish": "stop", "duration_s": 1.0, "text": frame
    })
    assert ok["core_valid"] is True
    assert ok["json_object"] is True
    assert frame not in json.dumps(ok, ensure_ascii=False)
    malformed = probe.summarize("B_two_systems", context, {
        "status": 200, "finish": "stop", "duration_s": 1.0, "text": "Resposta solta"
    })
    assert malformed["core_valid"] is False
    assert malformed["validator_code"] == "NOT_JSON"
    assert "Resposta solta" not in json.dumps(malformed, ensure_ascii=False)


def test_live_transport_not_called_when_preparing_variants(monkeypatch):
    monkeypatch.chdir(ROOT)
    probe = _probe()
    def forbidden_network(*args, **kwargs):
        raise AssertionError("The offline contract suite must not contact NVIDIA")
    monkeypatch.setattr(probe, "urlopen", forbidden_network)
    original = probe.original_prompt()
    context = probe.context_for(*probe.CASES[0])
    assert len(probe.make_payloads(context, original)) == 3
