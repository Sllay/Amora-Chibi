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
