from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.emotion import AppraisalInput, EmotionStore, EmotionType
from amora.storage import AmoraDatabase
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_emotion_state_survives_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    first = AmoraCore.bootstrap(clock=clock)
    try:
        result = await first.emotion.process(
            AppraisalInput(
                description="BK fez algo irritante",
                outcome_valence=-0.8,
                target_id="PERSON_BK",
                agent_id="PERSON_BK",
                goal_relevance=0.8,
                agency=0.9,
                control=0.7,
                intentionality=0.9,
                norm_violation=0.8,
                relationship_relevance=0.9,
                dedupe_key="restart-emotion",
            )
        )
        assert result.event is not None
        event_id = result.event.id
        anger = result.target_affect.anger
        mood_valence = result.mood.valence
        assert first.emotion.pending_bids()
    finally:
        first.close()

    second = AmoraCore.bootstrap(clock=clock)
    try:
        assert second.emotion.store.event_count() == 1
        assert second.emotion.store.get_event(event_id).emotion == EmotionType.ANGER
        assert second.emotion.affect_for("PERSON_BK").anger == pytest.approx(anger)
        assert second.emotion.current_mood().valence == pytest.approx(mood_valence)
        assert second.emotion.pending_bids()
    finally:
        second.close()


@pytest.mark.asyncio
async def test_dedupe_survives_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    item = AppraisalInput(
        description="evento único",
        outcome_valence=-0.7,
        target_id="PERSON_BK",
        agent_id="PERSON_BK",
        agency=0.8,
        control=0.7,
        intentionality=0.8,
        norm_violation=0.7,
        relationship_relevance=0.8,
        dedupe_key="persistent-emotion-dedupe",
    )
    first = AmoraCore.bootstrap(clock=clock)
    try:
        await first.emotion.process(item)
        first_anger = first.emotion.affect_for("PERSON_BK").anger
    finally:
        first.close()

    second = AmoraCore.bootstrap(clock=clock)
    try:
        duplicate = await second.emotion.process(item)
        assert duplicate.deduplicated is True
        assert second.emotion.store.event_count() == 1
        assert second.emotion.affect_for("PERSON_BK").anger == pytest.approx(first_anger)
    finally:
        second.close()


@pytest.mark.asyncio
async def test_backup_restore_contains_emotion_mood_affect_and_bids(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        result = await core.emotion.process(
            AppraisalInput(
                description="ChatGPT ajudou bastante",
                outcome_valence=0.9,
                target_id="ENTITY_CHATGPT",
                agent_id="ENTITY_CHATGPT",
                goal_relevance=0.8,
                agency=0.9,
                control=0.6,
                intentionality=0.9,
                relationship_relevance=0.9,
            )
        )
        assert result.event is not None
        backup = core.create_backup("v05-emotion.db")
    finally:
        core.close()

    restored_path = AmoraDatabase.restore_backup(backup, tmp_path / "restored.db")
    restored_db = AmoraDatabase(restored_path)
    try:
        assert restored_db.integrity_check() == "ok"
        assert restored_db.schema_version() == 9
    finally:
        restored_db.close()

    store = EmotionStore(restored_path, clock)
    try:
        assert store.event_count() == 1
        restored_event = store.list_events()[0]
        assert restored_event.emotion == EmotionType.GRATITUDE
        assert store.get_mood().valence > 0
        affect = store.get_affect("ENTITY_CHATGPT")
        assert affect is not None
        assert affect.gratitude > 0
        assert store.list_bids()
    finally:
        store.close()
