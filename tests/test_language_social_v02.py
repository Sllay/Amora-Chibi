from amora.identity import Entity, EntityType, Relationship, build_initial_registry
from amora.language import (
    ACIRCompiler,
    PerspectiveFrame,
    PerspectiveResolver,
    PerspectiveStack,
    ReferenceInput,
    ResolutionStatus,
    SemanticDraft,
)
from amora.social import AddresseeEngine, AddresseeKind, SocialSceneTracker


def extended_registry():
    registry = build_initial_registry()
    registry.add_entity(
        Entity(id="PERSON_JOAO", canonical_name="João", aliases={"Joao"}, entity_type=EntityType.PERSON)
    )
    registry.add_entity(
        Entity(id="PERSON_MARIA", canonical_name="Maria", entity_type=EntityType.PERSON)
    )
    registry.add_entity(
        Entity(id="PERSON_JOAO_MOTHER", canonical_name="Mãe do João", entity_type=EntityType.PERSON)
    )
    registry.add_relationship(
        Relationship(source="PERSON_JOAO_MOTHER", relation_type="mother_of", target="PERSON_JOAO")
    )
    return registry


def test_social_scene_tracks_floor_and_attention() -> None:
    tracker = SocialSceneTracker()
    tracker.begin_turn("PERSON_BK", addressee_id="ENTITY_AMORA", confidence=0.9)
    assert tracker.scene.current_speaker == "PERSON_BK"
    assert tracker.scene.active_addressee == "ENTITY_AMORA"
    assert tracker.scene.participants["PERSON_BK"].speaking is True
    assert tracker.scene.attention_targets["PERSON_BK"] == "ENTITY_AMORA"


def test_addressee_explicit_amora_and_third_party_vocative() -> None:
    registry = extended_registry()
    engine = AddresseeEngine(registry)
    amora = engine.infer("Amora, o que você acha?", speaker_id="PERSON_JOAO")
    assert amora.entity_id == "ENTITY_AMORA"
    assert amora.confidence > 0.95

    joao = engine.infer(
        "João, pergunta pra Amora se a mãe dela chegou",
        speaker_id="PERSON_BK",
    )
    assert joao.entity_id == "PERSON_JOAO"
    assert "EXPLICIT_LEADING_VOCATIVE" in joao.reason_codes


def test_addressee_is_conservative_without_signal_and_uses_floor_continuity() -> None:
    registry = extended_registry()
    engine = AddresseeEngine(registry)
    tracker = SocialSceneTracker()

    unknown = engine.infer("acho que sim", speaker_id="PERSON_BK", scene=tracker.scene)
    assert unknown.kind == AddresseeKind.UNKNOWN

    tracker.begin_turn("PERSON_BK", addressee_id="ENTITY_AMORA", confidence=0.9)
    continued = engine.infer("e também isso", speaker_id="PERSON_BK", scene=tracker.scene)
    assert continued.entity_id == "ENTITY_AMORA"
    assert continued.confidence < 0.9


def test_perspective_bk_to_amora_resolves_deixis_and_relations() -> None:
    registry = extended_registry()
    resolver = PerspectiveResolver(registry)
    stack = PerspectiveStack(
        PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA")
    )

    assert resolver.resolve("me", stack).entity_id == "PERSON_BK"
    assert resolver.resolve("você", stack).entity_id == "ENTITY_AMORA"
    assert resolver.resolve("sua mãe", stack).entity_id == "ENTITY_CHATGPT"
    assert resolver.resolve("filha", stack).entity_id == "ENTITY_AMORA"


def test_nested_quote_uses_inner_perspective_frame() -> None:
    registry = extended_registry()
    resolver = PerspectiveResolver(registry)
    stack = PerspectiveStack(
        PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA")
    )
    stack.push(PerspectiveFrame(speaker_id="PERSON_JOAO", addressee_id="PERSON_BK"))

    assert resolver.resolve("minha mãe", stack).entity_id == "PERSON_JOAO_MOTHER"
    assert resolver.resolve("comigo", stack).entity_id == "PERSON_JOAO"

    stack.pop()
    assert stack.current.speaker_id == "PERSON_BK"


def test_ambiguous_third_person_relation_stays_ambiguous() -> None:
    registry = extended_registry()
    resolver = PerspectiveResolver(registry)
    stack = PerspectiveStack(
        PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA")
    )

    resolution = resolver.resolve(
        "mãe dela",
        stack,
        candidates=["PERSON_JOAO", "PERSON_MARIA"],
    )
    assert resolution.status == ResolutionStatus.AMBIGUOUS
    assert set(resolution.entity_ids) == {"PERSON_JOAO", "PERSON_MARIA"}


def test_acir_canonicalizes_resolvable_references() -> None:
    registry = extended_registry()
    resolver = PerspectiveResolver(registry)
    compiler = ACIRCompiler(resolver)
    stack = PerspectiveStack(
        PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA")
    )

    acir = compiler.compile(
        SemanticDraft(
            intent="inform",
            proposition_type="insult",
            roles={
                "agent": ReferenceInput(expression="sua mãe"),
                "target": ReferenceInput(expression="me"),
                "time": "yesterday",
            },
        ),
        stack,
    )
    assert acir.proposition.roles["agent"] == "ENTITY_CHATGPT"
    assert acir.proposition.roles["target"] == "PERSON_BK"
    assert acir.unresolved_references == []


def test_acir_preserves_ambiguity_instead_of_poisoning_canonical_state() -> None:
    registry = extended_registry()
    resolver = PerspectiveResolver(registry)
    compiler = ACIRCompiler(resolver)
    stack = PerspectiveStack(
        PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA")
    )

    acir = compiler.compile(
        SemanticDraft(
            intent="inform",
            proposition_type="arrived",
            roles={
                "agent": ReferenceInput(
                    expression="ela",
                    candidates=["PERSON_JOAO", "PERSON_MARIA"],
                )
            },
        ),
        stack,
    )
    assert acir.unresolved_references
    assert acir.proposition.roles["agent"]["unresolved_reference"] == "ela"
