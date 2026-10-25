import asyncio
from datetime import datetime, timezone
from pathlib import Path

from amora.app import AmoraCore
from amora.conversation import ConversationStore, UtteranceKind, UtteranceStatus
from amora.events import PrivacyScope
from amora.storage import AmoraDatabase
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc))


def test_queued_speech_survives_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); clock = _clock(); first = AmoraCore.bootstrap(clock=clock)
    try:
        item = asyncio.run(first.conversation.enqueue_text("Papis?", target_id="PERSON_BK", kind=UtteranceKind.SEMANTIC, priority=70, privacy_scope=PrivacyScope.PERSONAL, source_event_id=None, dedupe_key="restart-v08-speech"))
        assert item is not None; utterance_id = item.id
    finally: first.close()
    second = AmoraCore.bootstrap(clock=clock)
    try:
        restored = second.conversation.store.get_utterance(utterance_id); assert restored is not None and restored.status == UtteranceStatus.QUEUED
        claimed = second.conversation.claim_next(); assert claimed is not None and claimed.id == utterance_id
    finally: second.close()


def test_backup_restore_preserves_speech_queue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); clock = _clock(); core = AmoraCore.bootstrap(clock=clock)
    try:
        item = asyncio.run(core.conversation.enqueue_text("Oi, papis.", target_id="PERSON_BK", kind=UtteranceKind.DIALOGUE, priority=70, privacy_scope=PrivacyScope.PERSONAL, source_event_id=None, dedupe_key="backup-v08-speech"))
        assert item is not None; backup = core.create_backup("v08-conversation.db")
    finally: core.close()
    restored_path = AmoraDatabase.restore_backup(backup, tmp_path / "restored.db"); restored_db = AmoraDatabase(restored_path)
    try:
        assert restored_db.integrity_check() == "ok" and restored_db.schema_version() == 11
    finally: restored_db.close()
    store = ConversationStore(restored_path, clock)
    try:
        assert store.pending_count() == 1
        restored = store.get_utterance_by_dedupe("backup-v08-speech"); assert restored is not None and restored.text == "Oi, papis."
    finally: store.close()
