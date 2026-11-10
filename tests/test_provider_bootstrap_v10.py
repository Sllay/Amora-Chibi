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


def test_default_social_is_groq_qwen_and_gemini_35_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.delenv("AMORA_SOCIAL_PRIMARY", raising=False)
    monkeypatch.setenv("AMORA_SECRET_GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("AMORA_SECRET_NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.setenv("AMORA_SECRET_GEMINI_API_KEY", "test-gemini-key")
    core = AmoraCore.bootstrap()
    try:
        assert core.health_report()["social_primary_provider"] == "groq"
        assert core.cognition.router.descriptor("groq").model == "qwen/qwen3.8-27b"
        assert core.cognition.router.descriptor("gemini").model == "gemini-3.5-flash-lite"
        assert core.cognition.router.descriptor("nvidia_mistral_large_4") is None
    finally:
        core.close()
