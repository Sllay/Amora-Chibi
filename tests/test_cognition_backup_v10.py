from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, CognitiveStore, ProviderDelta
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.storage import AmoraDatabase
from amora.time import SimulatedClock


class BackupProvider:
    def __init__(self) -> None:
        self.descriptor = ProviderDescriptor(
            name="backup-provider",
            model="backup-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=True,
            context_window_tokens=100_000,
            priority=90,
        )

    async def stream(self, context):
        yield ProviderDelta(text="Essa conversa fica comigo.", input_tokens=14, output_tokens=7, status_code=200)
        yield ProviderDelta(done=True, input_tokens=14, output_tokens=7, status_code=200)


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 6, 30, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_backup_restore_preserves_history_journal_replay_snapshot_and_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        core.cognition.register_provider(BackupProvider())
        request = CognitiveRequest(
            text="Guarda nosso contexto",
            speaker_id="PERSON_BK",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
        await core.cognition.respond(request)
        turn_id = request.turn_id
        backup = core.create_backup("v1-cognition.db")
        before_messages = core.cognition.store.message_count()
        before_journal = core.cognition.store.journal_count()
        before_usage = core.cognition.store.usage_ledger()
    finally:
        core.close()

    restored_path = AmoraDatabase.restore_backup(backup, tmp_path / "restored-v1.db")
    database = AmoraDatabase(restored_path)
    try:
        assert database.integrity_check() == "ok"
        assert database.schema_version() == 10
    finally:
        database.close()

    store = CognitiveStore(restored_path, clock)
    try:
        assert store.message_count() == before_messages
        assert store.journal_count() == before_journal
        assert store.list_messages(turn_id=turn_id)
        journal = store.journal_for_turn(turn_id)
        assert any("REPLAY_SNAPSHOT" in item.reason_codes for item in journal)
        assert store.usage_ledger() == before_usage
    finally:
        store.close()
