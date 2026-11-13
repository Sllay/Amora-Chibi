from amora.messaging.disclosure import public_text_allowed


def test_configured_confidential_literal_is_blocked(monkeypatch):
    monkeypatch.setenv("AMORA_CONFIDENTIAL_LITERALS_JSON", '["synthetic secret 123"]')
    assert not public_text_allowed("SYNTHETIC   SECRET 123!")
    assert public_text_allowed("ordinary public conversation")


def test_invalid_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("AMORA_CONFIDENTIAL_LITERALS_JSON", '{"bad": true}')
    assert not public_text_allowed("ordinary public conversation")


def test_default_configuration_does_not_block_normal_text(monkeypatch):
    monkeypatch.delenv("AMORA_CONFIDENTIAL_LITERALS_JSON", raising=False)
    assert public_text_allowed("ordinary public conversation")


async def test_rejected_group_realization_never_reaches_persistence(monkeypatch):
    from types import SimpleNamespace
    from amora.cognition.models import CognitiveRequest
    from amora.cognition.structured_runtime import CognitiveEngine

    monkeypatch.setenv("AMORA_CONFIDENTIAL_LITERALS_JSON", '["synthetic confidential marker"]')
    request = CognitiveRequest(
        text="hello", speaker_id="PERSON_TEST",
        metadata={"surface": "platform_messaging", "channel_type": "group"},
    )
    # Deliberately no store, normalizer or journal on this receiver: reaching
    # any persistence/realization operation would fail the test.
    plan = await CognitiveEngine._plan_from_validated(
        SimpleNamespace(), request,
        SimpleNamespace(text="synthetic confidential marker"),
        provider=None, model=None, on_segment=None, started=0,
        reason_codes=[], usage={},
    )
    assert plan.display_text == ""
    assert plan.speech_text == ""
    assert "CONFIDENTIAL_OUTPUT_SUPPRESSED" in plan.reason_codes
