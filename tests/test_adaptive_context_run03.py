"""RUN 03 foundation contracts; no real providers or private data."""
from types import SimpleNamespace

import pytest

from amora.cognition.adaptive_message import index_message, MessageDecompositionLimit
from amora.cognition.context_budget import (
    ContextBudgetExceeded, ensure_payload, measure_envelope, fit_provider_payload,
)
from test_phase2_context_contracts import context


def test_amd_preserves_unicode_whitespace_and_late_correction():
    original = (
        "  Eu gosto do carro A. Acho bonito.\r\n\r\n"
        + "Detalhes do primeiro carro. " * 35
        + "\nMas não vou comprar A. Escolhi o carro B. "
        + "Qual sua opinião sobre a imagem do B? 🚗  "
    )
    indexed = index_message(original, max_block_bytes=130)
    assert len(indexed.blocks) > 1
    assert "".join(indexed.read(original, i) for i in range(len(indexed.blocks))) == original
    for block in indexed.blocks:
        assert len(original[block.start:block.end].encode("utf-8")) <= 130
    hits = [(p.kind, original[p.span.start:p.span.end]) for p in indexed.pointers]
    assert ("negation", "não") in hits
    assert ("correction", "Mas") in hits
    assert ("decision", "Escolhi") in hits
    assert ("visual_reference", "imagem") in hits
    assert ("question", "?") in hits
    assert all(isinstance(v, int) for v in indexed.metadata()["pointer_counts"].values())
    with pytest.raises(ValueError, match="AMD_SOURCE_MISMATCH"):
        indexed.verify(original.replace("não", "sim"))


def test_amd_cross_boundary_marker_and_unbroken_unicode():
    # The phrase starts at a word boundary and can span the byte cap.
    original = "abc na verdade " + "🚀" * 25
    indexed = index_message(original, max_block_bytes=8)
    assert any(p.kind == "correction" and original[p.span.start:p.span.end] == "na verdade"
               for p in indexed.pointers)
    assert "".join(indexed.read(original, i) for i in range(len(indexed.blocks))) == original
    assert all(len(indexed.read(original, i).encode("utf-8")) <= 8
               for i in range(len(indexed.blocks)))


def test_amd_limits_never_return_partial_coverage():
    with pytest.raises(MessageDecompositionLimit):
        index_message("x" * 500, max_block_bytes=32, max_blocks=2)
    with pytest.raises(MessageDecompositionLimit):
        index_message("x" * 500, max_block_bytes=32, max_source_bytes=100)
    assert index_message("", max_block_bytes=128).blocks == ()


def test_envelope_reserves_output_and_distinguishes_bytes_from_estimate():
    ctx = context(token_budget=8192)
    descriptor = SimpleNamespace(context_window_tokens=1000, metadata={"max_request_bytes": 900})
    payload = {"input": "🚗" * 300, "max_output_tokens": 700}
    result = measure_envelope(ctx, payload, descriptor=descriptor)
    assert result.input_limit_tokens == 300
    assert result.output_reserve_tokens == 700
    assert result.estimated_input_tokens < result.input_limit_tokens
    assert result.body_bytes > 900
    assert not result.fits
    with pytest.raises(ContextBudgetExceeded):
        ensure_payload(ctx, payload, descriptor=descriptor)
    unknown = measure_envelope(ctx, payload)
    assert unknown.body_limit_bytes is None and unknown.fits


def test_provider_projection_retains_original_and_reserves_requested_output():
    ctx = context("Uma pergunta completa.")
    ctx.specialist_support = {"note": "x" * 3000}
    descriptor = SimpleNamespace(context_window_tokens=600, metadata={})
    def build(item):
        return {"input": item.user_text, "support": item.specialist_support, "max_tokens": 400}
    fitted, payload = fit_provider_payload(ctx, build, descriptor=descriptor)
    assert fitted.user_text == ctx.user_text
    assert fitted.specialist_support is None and ctx.specialist_support is not None
    assert measure_envelope(fitted, payload, descriptor=descriptor).fits


def test_wire_dedup_preserves_canonical_acir_and_resolves_reference():
    import json
    from amora.cognition.prompt_frame import render_turn_frame, turn_frame
    ctx = context(acir={"speaker": "PERSON_BK", "addressee": "ENTITY_AMORA",
                       "entities": ["PERSON_BK", "ENTITY_AMORA"],
                       "intent": "communicate", "proposition": {"roles": {"content": "LITERAL"}}})
    ctx.dialogue_plan["input_acir"] = ctx.acir
    baseline = turn_frame(ctx)
    wire = render_turn_frame(ctx)
    compact = json.loads(wire.split("\n", 1)[1])
    assert compact["dialogue_plan"]["input_acir"] == {"same_as": "current_turn.acir"}
    assert compact["current_turn"]["acir"] == baseline["current_turn"]["acir"]
    assert ctx.dialogue_plan["input_acir"] == ctx.acir
    assert "LITERAL" not in wire
    assert len(wire.split("\n", 1)[1]) < len(json.dumps(baseline, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
