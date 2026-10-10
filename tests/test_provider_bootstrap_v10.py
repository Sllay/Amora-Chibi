from __future__ import annotations

from pathlib import Path

from amora.app import AmoraCore


def test_gemini_cognition_bootstraps_without_groq(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.delenv("AMORA_SECRET_GROQ_API_KEY", raising=False)
    monkeypatch.setenv("AMORA_SECRET_GEMINI_API_KEY", "test-gemini-key")

    core = AmoraCore.bootstrap()
    try:
        assert core.cognition.router.descriptor("groq") is None
        gemini = core.cognition.router.descriptor("gemini")
        assert gemini is not None
        assert gemini.available is True
        assert core.health_report()["gemini_cognition_configured"] is True
        assert core.health_report()["groq_cognition_configured"] is False
    finally:
        core.close()
