from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRITICAL = [
    ROOT / "core/amora/cognition",
    ROOT / "core/amora/voice",
    ROOT / "core/amora/autonomy/integrated.py",
    ROOT / "core/amora/conversation/integrated.py",
    ROOT / "core/amora/models/scoped_router.py",
    ROOT / "core/amora/actions/skills.py",
    ROOT / "android/app/src/main/java/dev/amora/chibi/audio/BargeInDetector.kt",
    ROOT / "android/app/src/main/java/dev/amora/chibi/audio/AudioCaptureService.kt",
    ROOT / "android/app/src/main/java/dev/amora/chibi/core/CoreClient.kt",
    ROOT / "android/app/src/main/java/dev/amora/chibi/voice/VoiceOutput.kt",
]
FORBIDDEN = (
    "TODO",
    "FIXME",
    "NotImplementedError",
    "NotImplementedException",
    "TODO(",
)


def _files():
    for path in CRITICAL:
        if path.is_dir():
            yield from sorted(path.rglob("*.py"))
        else:
            yield path


def test_v10_critical_runtime_has_no_placeholder_markers() -> None:
    failures: list[str] = []
    for path in _files():
        assert path.exists(), f"critical V1 path missing: {path.relative_to(ROOT)}"
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN:
            if marker in text:
                failures.append(f"{path.relative_to(ROOT)} contains {marker}")
    assert not failures, "\n".join(failures)


def test_v10_layered_modules_are_wired_not_orphaned() -> None:
    cognition_init = (ROOT / "core/amora/cognition/__init__.py").read_text(encoding="utf-8")
    autonomy_init = (ROOT / "core/amora/autonomy/__init__.py").read_text(encoding="utf-8")
    app = (ROOT / "core/amora/app.py").read_text(encoding="utf-8")
    production = (ROOT / "core/amora/cognition/production.py").read_text(encoding="utf-8")
    resilient = (ROOT / "core/amora/cognition/resilient.py").read_text(encoding="utf-8")
    hardened = (ROOT / "core/amora/cognition/hardened.py").read_text(encoding="utf-8")
    context_v1 = (ROOT / "core/amora/cognition/context_v1.py").read_text(encoding="utf-8")
    providers_v1 = (ROOT / "core/amora/cognition/providers_v1.py").read_text(encoding="utf-8")
    store_v1 = (ROOT / "core/amora/cognition/store_v1.py").read_text(encoding="utf-8")

    assert "from .production import CognitiveEngine" in cognition_init
    assert "from .integrated import AutonomyEngine" in autonomy_init
    assert 'subscribe_bootstrap("cognition.action_suggested", autonomy.handle_cognitive_suggestion)' in app
    assert "ResilientCognitiveEngine" in production
    assert "HardenedCognitiveEngine" in resilient
    assert "BaseCognitiveEngine" in hardened
    assert "BaseContextCompiler" in context_v1
    assert "BaseGroqCognitiveProvider" in providers_v1
    assert "BaseCognitiveStore" in store_v1
