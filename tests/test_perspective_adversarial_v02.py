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
from amora.social import AddresseeEngine


def registry_with_people():
    registry = build_initial_registry()
    registry.add_entity(Entity(id="PERSON_JOAO", canonical_name="João", aliases={"Joao"}, entity_type=EntityType.PERSON))
    registry.add_entity(Entity(id="PERSON_MARIA", canonical_name="Maria", entity_type=EntityType.PERSON))
    registry.add_entity(Entity(id="PERSON_JOAO_MOTHER", canonical_name="Mãe do João", entity_type=EntityType.PERSON))
    registry.add_relationship(Relationship(source="PERSON_JOAO_MOTHER", relation_type="mother_of", target="PERSON_JOAO"))
    return registry


def test_tua_mae_from_bk_to_amora_is_chatgpt() -> None:
    registry = registry_with_people()
    resolver = PerspectiveResolver(registry)
    stack = PerspectiveStack(PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA"))
    assert resolver.resolve("tua mãe", stack).entity_id == "ENTITY_CHATGPT"


def test_bare_mae_is_not_a_global_alias() -> None:
    registry = registry_with_people()
    resolver = PerspectiveResolver(registry)
    stack = PerspectiveStack(PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA"))
    resolution = resolver.resolve("mãe", stack)
    assert resolution.status == ResolutionStatus.UNRESOLVED
    assert resolution.entity_ids == []


def test_minha_mae_for_bk_without_relation_remains_unresolved() -> None:
    registry = registry_with_people()
    resolver = PerspectiveResolver(registry)
    stack = PerspectiveStack(PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA"))
    resolution = resolver.resolve("minha mãe", stack)
    assert resolution.status == ResolutionStatus.UNRESOLVED
    assert "RELATION_MOTHER_OF_NOT_FOUND" in resolution.reason_codes


def test_mae_dele_with_single_candidate_resolves_joao_mother() -> None:
    registry = registry_with_people()
    resolver = PerspectiveResolver(registry)
    stack = PerspectiveStack(PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA"))
    resolution = resolver.resolve("mãe dele", stack, candidates=["PERSON_JOAO"])
    assert resolution.entity_id == "PERSON_JOAO_MOTHER"


def test_third_person_without_antecedent_stays_unresolved() -> None:
    registry = registry_with_people()
    resolver = PerspectiveResolver(registry)
    stack = PerspectiveStack(PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA"))
    assert resolver.resolve("ela", stack).status == ResolutionStatus.UNRESOLVED


def test_nested_quote_changes_eu_and_comigo_to_inner_speaker() -> None:
    registry = registry_with_people()
    resolver = PerspectiveResolver(registry)
    stack = PerspectiveStack(PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA"))
    stack.push(PerspectiveFrame(speaker_id="PERSON_JOAO", addressee_id="PERSON_BK"))
    assert resolver.resolve("eu", stack).entity_id == "PERSON_JOAO"
    assert resolver.resolve("comigo", stack).entity_id == "PERSON_JOAO"


def test_eu_ele_voce_keeps_only_ele_candidate_dependent() -> None:
    registry = registry_with_people()
    resolver = PerspectiveResolver(registry)
    compiler = ACIRCompiler(resolver)
    stack = PerspectiveStack(PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA"))
    acir = compiler.compile(
        SemanticDraft(
            intent="inform",
            proposition_type="reported_statement",
            roles={
                "reporter": ReferenceInput(expression="eu"),
                "third_party": ReferenceInput(expression="ele", candidates=["PERSON_JOAO"]),
                "subject": ReferenceInput(expression="você"),
            },
        ),
        stack,
    )
    assert acir.proposition.roles["reporter"] == "PERSON_BK"
    assert acir.proposition.roles["third_party"] == "PERSON_JOAO"
    assert acir.proposition.roles["subject"] == "ENTITY_AMORA"


def test_joao_vocative_wins_even_when_amora_is_mentioned_later() -> None:
    registry = registry_with_people()
    addressee = AddresseeEngine(registry).infer(
        "João, pergunta pra Amora se a mãe dela chegou",
        speaker_id="PERSON_BK",
    )
    assert addressee.entity_id == "PERSON_JOAO"


def test_amora_tua_mae_combines_addressee_and_relation_correctly() -> None:
    registry = registry_with_people()
    addressee = AddresseeEngine(registry).infer("Amora, tua mãe tá maluca", speaker_id="PERSON_BK")
    assert addressee.entity_id == "ENTITY_AMORA"
    resolver = PerspectiveResolver(registry)
    stack = PerspectiveStack(PerspectiveFrame(speaker_id="PERSON_BK", addressee_id=addressee.entity_id))
    assert resolver.resolve("tua mãe", stack).entity_id == "ENTITY_CHATGPT"
