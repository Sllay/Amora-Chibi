from datetime import datetime, timezone
from uuid import uuid4

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.time import SimulatedClock
from amora.world import EpistemicKind, EvidenceKind, EvidenceRef, WorldClaimDraft


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc))


def _record_relationship_hypothesis(
    core: AmoraCore,
    *,
    source_event_id=None,
    evidence_event_id=None,
    metadata: dict[str, object] | None = None,
    predicate: str = "child_of",
    subject_id: str = "ENTITY_AMORA",
    object_value: str = "PERSON_BK",
    confidence: float = 0.84,
):
    observed_event_id = source_event_id or uuid4()
    evidence_id = evidence_event_id or observed_event_id
    request = CognitiveRequest(
        text="evidência relacional observada",
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        source_event_id=observed_event_id,
        metadata=dict(metadata or {}),
    )
    core.cognition.observe_input(request)
    result = core.world.record(
        WorldClaimDraft(
            kind=EpistemicKind.HYPOTHESIS,
            statement=f"{subject_id} {predicate} {object_value}",
            subject_id=subject_id,
            predicate=predicate,
            object_value=object_value,
            source="cognition:test-provider",
            source_entity_id="ENTITY_AMORA",
            confidence=confidence,
            evidence=[
                EvidenceRef(
                    kind=EvidenceKind.EVENT,
                    ref_id=str(evidence_id),
                    confidence=confidence,
                    note="independent observed turn",
                )
            ],
            metadata={"turn_id": str(request.turn_id)},
        )
    )
    return request, result.claim


def test_two_independent_events_learn_relationship_after_corroboration(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        _record_relationship_hypothesis(core)
        assert core.identities.relationship("ENTITY_AMORA", "child_of", "PERSON_BK") is None
        first_state = core.cognition.compiler.relationship_learning.state()
        assert first_state["last_reason_code"] == "RELATIONSHIP_NEEDS_CORROBORATION"
        assert first_state["last_evidence_count"] == 1

        _record_relationship_hypothesis(core)
        learned = core.identities.relationship("ENTITY_AMORA", "child_of", "PERSON_BK")
        assert learned is not None
        assert learned.confidence >= 0.84

        stored = core.world_store.current_relationship("ENTITY_AMORA", "child_of", "PERSON_BK")
        assert stored is not None
        assert stored.source_of_truth == "cognition:corroborated_relationship"
        assert len(stored.metadata["evidence_event_ids"]) == 2
        assert core.cognition.compiler.relationship_learning.state()["learned_count"] == 1
    finally:
        core.close()


def test_duplicate_event_does_not_count_as_independent_corroboration(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        same_evidence = uuid4()
        _record_relationship_hypothesis(core, evidence_event_id=same_evidence)
        _record_relationship_hypothesis(core, evidence_event_id=same_evidence)

        assert core.identities.relationship("ENTITY_AMORA", "child_of", "PERSON_BK") is None
        state = core.cognition.compiler.relationship_learning.state()
        assert state["last_reason_code"] == "RELATIONSHIP_NEEDS_CORROBORATION"
        assert state["last_evidence_count"] == 1
    finally:
        core.close()


def test_relationship_learning_flag_and_test_mode_are_real_gates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        _record_relationship_hypothesis(core, metadata={"relationship_learning": False})
        assert core.cognition.compiler.relationship_learning.state()["last_reason_code"] == "RELATIONSHIP_TURN_NOT_ELIGIBLE"

        _record_relationship_hypothesis(core, metadata={"test_mode": True, "relationship_learning": True})
        assert core.cognition.compiler.relationship_learning.state()["last_reason_code"] == "RELATIONSHIP_TURN_NOT_ELIGIBLE"
        assert core.identities.relationship("ENTITY_AMORA", "child_of", "PERSON_BK") is None
    finally:
        core.close()


def test_learning_rejects_relationship_endpoints_outside_active_social_pair(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        _record_relationship_hypothesis(
            core,
            predicate="mother_of",
            subject_id="ENTITY_CHATGPT",
            object_value="PERSON_BK",
        )
        state = core.cognition.compiler.relationship_learning.state()
        assert state["last_reason_code"] == "RELATIONSHIP_ENDPOINT_OUTSIDE_ACTIVE_PAIR"
        assert core.identities.relationship("ENTITY_CHATGPT", "mother_of", "PERSON_BK") is None
    finally:
        core.close()


def test_existing_stronger_relationship_is_never_downgraded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        before = core.identities.relationship("PERSON_BK", "father_of", "ENTITY_AMORA")
        assert before is not None and before.confidence == 1.0

        _record_relationship_hypothesis(
            core,
            predicate="father_of",
            subject_id="PERSON_BK",
            object_value="ENTITY_AMORA",
        )
        _record_relationship_hypothesis(
            core,
            predicate="father_of",
            subject_id="PERSON_BK",
            object_value="ENTITY_AMORA",
        )

        after = core.identities.relationship("PERSON_BK", "father_of", "ENTITY_AMORA")
        assert after is not None and after.confidence == 1.0
        state = core.cognition.compiler.relationship_learning.state()
        assert state["last_reason_code"] == "RELATIONSHIP_EXISTING_STATE_STRONGER"
    finally:
        core.close()


def test_learned_relationship_rehydrates_registry_after_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    first = AmoraCore.bootstrap(clock=_clock())
    try:
        _record_relationship_hypothesis(first)
        _record_relationship_hypothesis(first)
        assert first.identities.relationship("ENTITY_AMORA", "child_of", "PERSON_BK") is not None
    finally:
        first.close()

    second = AmoraCore.bootstrap(clock=_clock())
    try:
        restored = second.identities.relationship("ENTITY_AMORA", "child_of", "PERSON_BK")
        assert restored is not None
        stored = second.world_store.current_relationship("ENTITY_AMORA", "child_of", "PERSON_BK")
        assert stored is not None
        assert restored.confidence == stored.confidence
    finally:
        second.close()


def test_world_claim_observer_failure_is_observable_but_does_not_break_world_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        def broken_observer(_claim):
            raise RuntimeError("observer exploded")

        core.world.subscribe_claim_observer(broken_observer)
        _, claim = _record_relationship_hypothesis(core)

        assert core.world.store.get_claim(claim.id) is not None
        observer_state = core.world.claim_observer_state()
        assert observer_state["error_count"] == 1
        assert "RuntimeError" in str(observer_state["last_error"])
    finally:
        core.close()
