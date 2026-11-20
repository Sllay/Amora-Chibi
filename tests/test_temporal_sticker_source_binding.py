"""A derived animation frame sheet is evidence, never the asset's source SHA."""
import base64
import hashlib
from pathlib import Path

import pytest

from amora.messaging import MessagingPlatform
from amora.messaging.visual_enrichment import (
    VisualEnrichmentError, VisualEnrichmentStatus, VisualExpressionEnrichmentService,
)
from test_visual_enrichment_service_ch2 import (
    FakeAnalyzer, _core, _sticker, _message, _observe, _claim,
)


SOURCE_GIF = b"GIF89a" + b"four-frame-native-source-fixture"
PNG = b"\x89PNG\r\n\x1a\n"
STORYBOARD = (
    PNG + (13).to_bytes(4, "big") + b"IHDR"
    + (480).to_bytes(4, "big") + (480).to_bytes(4, "big")
    + b"\x08\x06\x00\x00\x00" + b"\x00" * 12
)


def apng_source():
    # A chunk-valid APNG *control envelope*; actual frame decoding takes
    # place on the worker via ffmpeg and is covered by the Node suite.
    ihdr = (13).to_bytes(4, "big") + b"IHDR" + (64).to_bytes(4, "big") * 2
    ihdr += b"\x08\x06\x00\x00\x00" + b"\x00" * 4
    actl = (8).to_bytes(4, "big") + b"acTL" + (2).to_bytes(4, "big") + (0).to_bytes(4, "big")
    actl += b"\x00" * 4
    return PNG + ihdr + actl


class CapturingAnalyzer(FakeAnalyzer):
    def __init__(self):
        super().__init__()
        self.tags = []

    async def analyze(self, *, content, mime, name, description, tags):
        self.tags.append(tags)
        return await super().analyze(
            content=content, mime=mime, name=name, description=description, tags=tags)


async def submit(service, claim, *, source=SOURCE_GIF, mime="image/gif",
                 kind="gif", derived=STORYBOARD):
    return await service.ingest_base64(
        platform=MessagingPlatform.DISCORD, account_scope="primary",
        scope_id=claim.record.scope_id, kind=claim.record.kind,
        native_id=claim.record.native_id,
        expected_fingerprint=claim.record.fingerprint,
        claim_token=claim.claim_token, content_mime=mime,
        content_base64=base64.b64encode(source).decode(),
        temporal_kind=kind,
        temporal_storyboard_base64=base64.b64encode(derived).decode()
        if derived is not None else None,
    )


@pytest.mark.asyncio
async def test_visual_model_sees_storyboard_while_memory_hashes_original_gif(tmp_path, monkeypatch):
    core = _core(tmp_path, monkeypatch)
    analyzer = CapturingAnalyzer()
    service = VisualExpressionEnrichmentService(core.messaging.expression_memory, analyzer=analyzer)
    try:
        _observe(core, service, _message(_sticker(format_type=4)))
        claim = _claim(service)
        result = await submit(service, claim)
        assert result.status == VisualEnrichmentStatus.ENRICHED
        assert result.content_fingerprint == hashlib.sha256(SOURCE_GIF).hexdigest()
        assert analyzer.seen == [(STORYBOARD, "image/png")]
        assert "__amora_temporal_storyboard_v1__" in analyzer.tags[0]
        record = core.messaging.expression_memory.get(
            platform=MessagingPlatform.DISCORD, account_scope="primary",
            scope_id="guild-1", kind="sticker", native_id=_sticker().native_id)
        assert record.content_fingerprint == hashlib.sha256(SOURCE_GIF).hexdigest()
        assert record.content_mime == "image/gif"
        assert "temporal_v1" in record.visual_provider

        # Already committed origin identity cannot be re-analyzed under a new
        # temporary contact-sheet rendering or ambiguous HTTP retry.
        again = await submit(service, claim)
        assert again.status == VisualEnrichmentStatus.REUSED
        assert analyzer.calls == 1
    finally:
        core.close()


@pytest.mark.asyncio
async def test_apng_source_requires_real_animation_control_chunk(tmp_path, monkeypatch):
    core = _core(tmp_path, monkeypatch)
    analyzer = CapturingAnalyzer()
    service = VisualExpressionEnrichmentService(core.messaging.expression_memory, analyzer=analyzer)
    try:
        _observe(core, service, _message(_sticker(format_type=2)))
        claim = _claim(service)
        with pytest.raises(VisualEnrichmentError) as exc:
            await submit(service, claim, source=PNG + b"fake acTL", mime="image/png", kind="apng")
        assert exc.value.code == "INVALID_TEMPORAL_STORYBOARD"
        assert analyzer.calls == 0

        result = await submit(service, claim, source=apng_source(),
                              mime="image/png", kind="apng")
        assert result.status == VisualEnrichmentStatus.ENRICHED
        assert result.content_fingerprint == hashlib.sha256(apng_source()).hexdigest()
        assert analyzer.seen[0] == (STORYBOARD, "image/png")
    finally:
        core.close()


@pytest.mark.asyncio
async def test_temporal_render_never_overrides_denied_source_consent(tmp_path, monkeypatch):
    core = _core(tmp_path, monkeypatch)
    analyzer = CapturingAnalyzer()
    service = VisualExpressionEnrichmentService(core.messaging.expression_memory, analyzer=analyzer)
    try:
        _observe(core, service, _message(_sticker(format_type=4), remote_processing_allowed=False))
        claim = _claim(service)
        result = await submit(service, claim)
        assert result.status == VisualEnrichmentStatus.DEFERRED
        assert result.reason_code == "REMOTE_PROCESSING_DENIED"
        assert analyzer.calls == 0
    finally:
        core.close()
