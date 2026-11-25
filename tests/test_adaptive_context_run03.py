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
    assert json.loads(render_turn_frame(ctx).split("\n", 1)[1]) == baseline
    wire = render_turn_frame(ctx, compact_acir=True)
    compact = json.loads(wire.split("\n", 1)[1])
    assert compact["dialogue_plan"]["input_acir"] == {"same_as": "current_turn.acir"}
    assert compact["current_turn"]["acir"] == baseline["current_turn"]["acir"]
    assert ctx.dialogue_plan["input_acir"] == ctx.acir
    assert "LITERAL" not in wire
    assert len(wire.split("\n", 1)[1]) < len(json.dumps(baseline, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def test_global_map_preserves_late_negation_and_question_and_rejects_invented_quote():
    from amora.cognition.adaptive_message import (
        AdaptiveEvidenceError, bind_evidence, merge_evidence, bind_consolidation,
    )
    original = "Gosto do carro A. " + "x" * 120 + "\nMas não vou comprar A. Escolhi B. Qual a opinião da imagem?"
    index = index_message(original, max_block_bytes=90)
    rows = []
    for i, block in enumerate(index.blocks):
        lo = max(0, block.start - 160)
        hi = min(len(original), block.end + 160)
        literal = original[block.start:block.end]
        # Exact excerpts from each core span; include any boundary-spanning signals.
        for p in index.pointers:
            if p.block == i and p.span.end > block.end:
                literal = original[block.start:p.span.end]
        window = original[lo:hi]
        occurrence = 0
        cursor = window.find(literal)
        while cursor != block.start - lo:
            assert cursor >= 0
            occurrence += 1
            cursor = window.find(literal, cursor + 1)
        item = {"kind": "claim", "quote": literal, "occurrence": occurrence,
                "interpretation": "source excerpt"}
        raw = {"review_complete": True, "items": [item]}
        rows.append(bind_evidence(original, index, i, raw))
        with pytest.raises(AdaptiveEvidenceError):
            bind_evidence(original, index, i, {"review_complete": True,
                "items": [{**item, "quote": "invented quote"}]})
    evidence = merge_evidence(index, rows)
    assert [item["start"] for item in evidence] == sorted(item["start"] for item in evidence)
    ids = [item["id"] for item in evidence]
    groups = bind_consolidation(evidence, {"groups": [
        {"ids": ids, "state": "uncertain", "interpretation": "A rejeitado; B escolhido; pergunta visual"}]})
    assert groups[0]["ids"] == ids
    with pytest.raises(AdaptiveEvidenceError):
        bind_consolidation(evidence, {"groups": [
            {"ids": ids[:-1], "state": "active", "interpretation": "missing evidence"}]})
    with pytest.raises(AdaptiveEvidenceError):
        merge_evidence(index, rows[:-1])


@pytest.mark.asyncio
async def test_groq_adaptive_pilot_all_blocks_then_global_then_final(monkeypatch):
    import json
    import httpx
    from amora.cognition.providers_identity import GroqCognitiveProvider
    from amora.cognition.durable_realization import (
        DURABLE_REALIZATION, DURABLE_AUTHORIZATION, DeferredRealization,
    )
    from amora.cognition.prompt_frame import render_current_turn
    from amora.events import PrivacyScope

    ctx = context("Gosto do carro A. " + "x" * 5000 + " Mas não vou comprar A. Escolhi B. Qual a opinião da imagem?")
    ctx.privacy_scope = PrivacyScope.PERSONAL
    provider = GroqCognitiveProvider(SimpleNamespace(get=lambda name: "synthetic" if name == "GROQ_API_KEY" else None))
    observed = []
    guard_calls = []
    def root(value):
        return {"status": "completed", "output": [{"type": "message",
            "content": [{"type": "output_text", "text": json.dumps(value)}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}

    async def fake_post(payload, *, api_key, error_label):
        data = json.loads(payload["input"][0]["content"])
        assert payload["store"] is False
        observed.append(data)
        if "literal_data" in data:
            literal = data["literal_data"]
            if data["block"] == 0:
                quote = "Gosto do carro A."
            else:
                quote = "Mas não vou comprar A. Escolhi B. Qual a opinião da imagem?"
            assert quote in literal
            value = {"review_complete": True, "items": [{"kind": "claim", "quote": quote,
                "occurrence": 0, "interpretation": "unverified"}]}
        else:
            value = {"groups": [{"ids": [item["id"] for item in data["evidence"]],
                "state": "uncertain", "interpretation": "B escolhido, A rejeitado; pergunta visual"}]}
        return root(value), httpx.Response(200)

    async def fake_final(projected, *, api_key):
        assert projected.user_text == ctx.user_text
        assert projected.dialogue_plan == ctx.dialogue_plan
        assert "_adaptive_projection" not in projected.model_dump()
        mapped = json.loads(render_current_turn(projected).split("\n", 1)[1])
        observed.append(mapped)
        assert "não vou comprar A" in str(mapped)
        assert "Qual a opinião da imagem?" in str(mapped)
        return {"moves": [{"kind": "answer_question", "text": "B, não A."}],
                "closing": "statement", "_usage": {"input_tokens": 20, "output_tokens": 6}}

    async def guard():
        guard_calls.append(True)
        return True
    monkeypatch.setattr(provider, "_post_responses", fake_post)
    monkeypatch.setattr(provider, "_responses_realize", fake_final)
    turn = DURABLE_REALIZATION.set(str(ctx.turn_id))
    auth = DURABLE_AUTHORIZATION.set(guard)
    try:
        result = await provider.realize_adaptive(ctx)
        assert len(observed) == 4 and len(guard_calls) == 4
        assert result["_usage"]["amd_blocks"] == 2
        assert result["_usage"]["input_tokens"] == 50
        assert ctx._adaptive_projection is None
    finally:
        DURABLE_AUTHORIZATION.reset(auth)
        DURABLE_REALIZATION.reset(turn)
    with pytest.raises(ContextBudgetExceeded):
        await provider.realize_adaptive(ctx)
    assert len(observed) == 4

    async def revoked():
        return False
    turn = DURABLE_REALIZATION.set(str(ctx.turn_id))
    auth = DURABLE_AUTHORIZATION.set(revoked)
    try:
        with pytest.raises(DeferredRealization, match="DURABLE_REALIZATION_DEFERRED"):
            await provider.realize_adaptive(ctx)
        assert len(observed) == 4
    finally:
        DURABLE_AUTHORIZATION.reset(auth)
        DURABLE_REALIZATION.reset(turn)


def test_runtime_memory_query_projects_oversized_terms_without_rewriting_source():
    from amora.memory.store import _retrieval_tokens
    from amora.storage.postgres_fts_bm25_lab import normalized_fts5_terms, BM25LabError

    opaque = "a" * 4400
    original = opaque + " café coelho ação"
    assert _retrieval_tokens(original) == ["ação", "café", "coelho"]
    assert normalized_fts5_terms(original, runtime_projection=True) == ["acao", "cafe", "coelho"]
    assert original.startswith(opaque)
    assert normalized_fts5_terms(opaque, runtime_projection=True) == []
    assert normalized_fts5_terms("!? 🚗", runtime_projection=True) == []
    assert normalized_fts5_terms("café", runtime_projection=True) == normalized_fts5_terms("café")
    # The strict synthetic-lab API has not been relaxed.
    with pytest.raises(BM25LabError, match="BM25_QUERY_TOKEN_TOO_LONG"):
        normalized_fts5_terms(opaque)
    # Invalid terms must not occupy any of the 16 available query slots.
    words = " ".join(f"termo{i:02d}" for i in range(20))
    assert _retrieval_tokens(opaque + " " + words) == _retrieval_tokens(words)


def test_context_rejection_is_not_generic_http400_or_safety_rejection():
    from amora.cognition.providers import ProviderError
    from amora.cognition.provider_resilience import context_rejection, classify_provider_failure
    for status, category, expected in [
        (413, "other", True), (400, "context_length_exceeded", True),
        (400, "json_generation_failed", False), (400, "explicit_content_policy_rejection", False),
        (429, "context_length_exceeded", False),
    ]:
        exc = ProviderError("synthetic", status_code=status, diagnostics={"provider_error_category":category})
        assert context_rejection(exc) is expected
        assert (classify_provider_failure(exc).code == "PROVIDER_INPUT_TOO_LARGE") is expected
