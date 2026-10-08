from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.language import ACIR, ACIRProposition, UnresolvedReference
from amora.time import SimulatedClock
from amora.world import (
    EpistemicKind,
    PerspectiveClaimDraft,
    PerspectiveConsolidationStatus,
    SelfLayer,
    WorldClaimDraft,
)


def _core(tmp_path: Path, monkeypatch, clock: SimulatedClock | None = None) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap(clock=clock)


def test_report_is_not_silently_promoted_to_canonical_fact(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        result = core.world.record(
            WorldClaimDraft(
                kind=EpistemicKind.REPORT,
                statement="BK disse que gosta de café",
                subject_id="PERSON_BK",
                predicate="likes_coffee",
                object_value=True,
                source="test",
                source_entity_id="PERSON_BK",
                confidence=1.0,
            )
        )
        assert result.claim.canonical is False
        assert core.world.current_state(subject_id="PERSON_BK") == []
        assert "NONCANONICAL_EPISTEMIC_RECORD" in result.reason_codes
    finally:
        core.close()


def test_belief_revision_preserves_history_and_temporal_validity(tmp_path: Path, monkeypatch) -> None:
    clock = SimulatedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    core = _core(tmp_path, monkeypatch, clock)
    try:
        old = core.world.record(
            WorldClaimDraft(
                kind=EpistemicKind.BELIEF,
                statement="BK não gosta de café",
                subject_id="PERSON_BK",
                predicate="likes_coffee",
                object_value=False,
                source="test:first",
                confidence=0.98,
            )
        ).claim
        assert old.canonical is True
        assert core.world.current_state(subject_id="PERSON_BK")[0].object_value is False

        clock.advance(timedelta(days=1))
        new_result = core.world.record(
            WorldClaimDraft(
                kind=EpistemicKind.BELIEF,
                statement="BK agora gosta de café",
                subject_id="PERSON_BK",
                predicate="likes_coffee",
                object_value=True,
                source="test:revision",
                confidence=0.97,
            )
        )
        new = new_result.claim
        assert new.supersedes_claim_id == old.id
        assert "BELIEF_REVISION" in new_result.reason_codes
        assert "EXPECTATION_CHANGED" in new_result.reason_codes
        current = core.world.current_state(subject_id="PERSON_BK")
        assert len(current) == 1
        assert current[0].object_value is True

        history = core.world.history(subject_id="PERSON_BK", predicate="likes_coffee")
        assert len(history) == 2
        previous = next(item for item in history if item.id == old.id)
        assert previous.valid_until == new.valid_from
    finally:
        core.close()


def test_low_confidence_belief_and_hypothesis_do_not_enter_canonical_state(tmp_path: Path, monkeypatch) -> None:
    clock = SimulatedClock(datetime(2026, 2, 1, tzinfo=timezone.utc))
    core = _core(tmp_path, monkeypatch, clock)
    try:
        uncertain = core.world.record(
            WorldClaimDraft(
                kind=EpistemicKind.BELIEF,
                statement="Talvez BK goste de chá",
                subject_id="PERSON_BK",
                predicate="likes_tea",
                object_value=True,
                source="test",
                confidence=0.62,
            )
        ).claim
        assert uncertain.canonical is False

        hypothesis = core.world.record(
            WorldClaimDraft(
                kind=EpistemicKind.HYPOTHESIS,
                statement="BK pode estar desenhando agora",
                subject_id="PERSON_BK",
                predicate="current_activity",
                object_value="drawing",
                source="test",
                confidence=0.55,
            )
        ).claim
        assert hypothesis.canonical is False
        assert hypothesis.valid_until == hypothesis.valid_from + timedelta(hours=24)
        assert core.world.current_state(subject_id="PERSON_BK") == []
    finally:
        core.close()


def test_auth_content_cannot_enter_world_model(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError, match="AUTH"):
            core.world.record(
                WorldClaimDraft(
                    kind=EpistemicKind.OBSERVATION,
                    statement="token secreto",
                    predicate="secret",
                    object_value="abc",
                    source="test",
                    privacy_scope=PrivacyScope.AUTH,
                )
            )
        assert core.world_store.claim_count() == 0
    finally:
        core.close()


def test_self_core_identity_is_protected_but_preferences_can_evolve(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        entity_id = core.self_model.get(SelfLayer.CORE_IDENTITY, "entity_id")
        assert entity_id is not None
        assert entity_id.value == "ENTITY_AMORA"
        assert entity_id.locked is True
        with pytest.raises(PermissionError):
            core.self_model.set(SelfLayer.CORE_IDENTITY, "entity_id", "SOMEONE_ELSE", source="test")

        first = core.self_model.set(SelfLayer.PREFERENCES, "favorite_snack", "morango", source="test", confidence=0.7)
        second = core.self_model.set(SelfLayer.PREFERENCES, "favorite_snack", "chocolate", source="test", confidence=0.8)
        assert first.id == second.id
        assert core.self_model.get(SelfLayer.PREFERENCES, "favorite_snack").value == "chocolate"
    finally:
        core.close()


def test_relationship_evolution_preserves_revision_history_without_affect(tmp_path: Path, monkeypatch) -> None:
    clock = SimulatedClock(datetime(2026, 3, 1, tzinfo=timezone.utc))
    core = _core(tmp_path, monkeypatch, clock)
    try:
        initial = core.world_store.current_relationship("PERSON_BK", "father_of", "ENTITY_AMORA")
        assert initial is not None
        assert initial.source_of_truth == "bootstrap:entity_registry"

        clock.advance(timedelta(days=1))
        revised = core.relationships.record(
            "PERSON_BK",
            "father_of",
            "ENTITY_AMORA",
            confidence=0.96,
            source_of_truth="test:corroborated",
            metadata={"reason": "additional evidence"},
        )
        assert revised.supersedes_id == initial.id
        history = core.relationships.history("PERSON_BK", target_id="ENTITY_AMORA")
        father_history = [item for item in history if item.relation_type == "father_of"]
        assert len(father_history) == 2
        assert all("affinity" not in item.metadata and "anger" not in item.metadata for item in father_history)
    finally:
        core.close()


def test_perspective_consolidation_defers_ambiguity_and_accepts_canonical_roles(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        ambiguous = ACIR(
            speaker="PERSON_BK",
            addressee="ENTITY_AMORA",
            intent="inform",
            entities=["PERSON_BK", "ENTITY_AMORA"],
            proposition=ACIRProposition(type="preference", roles={"subject": {"unresolved_reference": "ele", "candidates": []}, "object": "café"}),
            unresolved_references=[
                UnresolvedReference(role="subject", expression="ele", candidates=[], confidence=0.2, reason_codes=["NO_ANTECEDENT"])
            ],
            confidence=0.2,
        )
        deferred = core.perspective_consolidator.consolidate(
            ambiguous,
            PerspectiveClaimDraft(statement="Ele gosta de café", predicate="likes_drink"),
        )
        assert deferred.status == PerspectiveConsolidationStatus.DEFERRED
        assert core.world_store.claim_count() == 0

        resolved = ACIR(
            speaker="PERSON_BK",
            addressee="ENTITY_AMORA",
            intent="inform",
            entities=["PERSON_BK", "ENTITY_AMORA"],
            proposition=ACIRProposition(type="preference", roles={"subject": "PERSON_BK", "object": "café"}),
            confidence=0.99,
        )
        consolidated = core.perspective_consolidator.consolidate(
            resolved,
            PerspectiveClaimDraft(kind=EpistemicKind.REPORT, statement="BK diz que gosta de café", predicate="likes_drink"),
        )
        assert consolidated.status == PerspectiveConsolidationStatus.CONSOLIDATED
        assert consolidated.claim is not None
        assert consolidated.claim.subject_id == "PERSON_BK"
        assert consolidated.claim.object_value == "café"
        assert consolidated.claim.source_entity_id == "PERSON_BK"
        assert consolidated.claim.canonical is False
    finally:
        core.close()


def test_world_state_survives_restart(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / ".amora"
    monkeypatch.setenv("AMORA_HOME", str(home))
    first = AmoraCore.bootstrap()
    try:
        first.world.record(
            WorldClaimDraft(
                kind=EpistemicKind.BELIEF,
                statement="BK prefere desenhar à noite",
                subject_id="PERSON_BK",
                predicate="preferred_drawing_period",
                object_value="night",
                source="test",
                confidence=0.96,
            )
        )
    finally:
        first.close()

    second = AmoraCore.bootstrap()
    try:
        state = second.world.current_state(subject_id="PERSON_BK")
        assert len(state) == 1
        assert state[0].predicate == "preferred_drawing_period"
        assert state[0].object_value == "night"
        assert second.health_report()["schema_version"] == 5
    finally:
        second.close()
