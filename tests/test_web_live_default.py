"""Live Web chat defaults to persistence, while explicit test turns remain isolated."""
from pathlib import Path

from amora.web.routes import WebTextTurnRequest


def test_web_turn_defaults_to_live_memory_mode():
    request = WebTextTurnRequest(text="Oi, Amora")
    assert request.test_mode is False


def test_explicit_test_mode_remains_available():
    request = WebTextTurnRequest(text="Teste técnico", test_mode=True)
    assert request.test_mode is True


def test_web_client_does_not_enable_test_mode_by_default():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web/index.html").read_text(encoding="utf-8")
    script = (root / "web/app-v2.js").read_text(encoding="utf-8")
    assert '<input id="testToggle" type="checkbox" />' in html
    assert 'testToggle.checked = browserStore.get(TEST_KEY) === "1";' in script
    assert 'const TEST_KEY = "amora.web.test.v2";' in script
    assert "test_mode: testToggle.checked" in script
