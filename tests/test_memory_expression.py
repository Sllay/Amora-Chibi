from __future__ import annotations

from types import SimpleNamespace

import pytest

from amora.memory.models import ProvenanceKind
from amora.messaging.memory_expression import memory_clause, render_memory_reply


def record(*, kind=ProvenanceKind.USER_ASSERTION, actor="PERSON_BK", owner="PERSON_BK", source="test"):
    return SimpleNamespace(owner_entity_id=owner,
        provenance=SimpleNamespace(kind=kind, actor_id=actor, source=source))


ORQ = "O falante está criando um projeto chamado Orquídea, que será seu próximo jogo de sobrevivência com cinco mapas."
AUR = "Relato de PERSON_BK: estou criando um sistema chamado Aurora. Ele será responsável pela geração procedural de biomas nos meus jogos."


def test_real_reflection_perspective_is_natural_and_fact_preserving():
    r = record(kind=ProvenanceKind.INFERENCE, actor="ENTITY_AMORA", source="cognition:groq")
    text = render_memory_reply([(r, ORQ)])
    assert text == ("Lembro sim! Você me contou que está criando um projeto chamado Orquídea, "
                    "que será seu próximo jogo de sobrevivência com cinco mapas.")
    assert "O falante" not in text
    assert "registro" not in text


def test_explicit_owner_source_preserves_both_sentences():
    text = render_memory_reply([(record(), AUR)])
    assert text == ("Lembro sim! Você me contou que está criando um sistema chamado Aurora. "
                    "Ele será responsável pela geração procedural de biomas nos seus jogos.")


@pytest.mark.parametrize("source", [
    "Minha amiga disse que meu jogo é dela.",
    "Estou criando um projeto e perdi meus arquivos.",
    "Eu quero um jogo. Criei três mapas.",
    'Meu amigo disse: "Eu quero meu jogo".',
    "Estou criando um projeto. Meu irmão também.",
])
def test_complex_perspective_preserves_verbatim(source):
    assert memory_clause(record(), source) == "Você me contou: “" + source + "”"


def test_not_reassign_unowned_or_unknown_provenance():
    assert ORQ in memory_clause(record(owner="OTHER"), ORQ)
    assert ORQ in memory_clause(record(kind=ProvenanceKind.INFERENCE, actor="ENTITY_AMORA",
                                       source="unverified"), ORQ)


def test_generic_and_multiple_records_do_not_invent_details():
    r = record()
    text = render_memory_reply([(r, "Tenho cinco mapas."), (r, "Tenho cinco mapas."),
                                (r, "Meu sistema terá biomas.")], generic=True)
    assert text.count("cinco mapas") == 1
    assert "seu sistema terá biomas" in text
    assert "Lembro de algumas coisas" in text


@pytest.mark.asyncio
async def test_discord_natural_recall_and_disclosure_keep_authority_and_literal_guards(tmp_path, monkeypatch):
    from amora.app import AmoraCore
    from amora.time import SimulatedClock
    from test_platform_messaging_step2 import _bind_bk
    from test_persistent_owner_recall import _dm
    from test_owner_memory_disclosure import _group
    from test_reflection_v10 import StructuredProvider

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        class Provider(StructuredProvider):
            async def propose(self, context):
                bundle = await super().propose(context)
                bundle["memory_candidates"][0]["content"] = ORQ
                bundle["memory_candidates"][0]["summary"] = ORQ
                bundle["belief_hypotheses"] = []
                bundle["action_suggestions"] = []
                return bundle
        core.cognition.register_provider(Provider())
        msg = _dm(core, "natural-source", "Estou criando Orquídea, meu próximo jogo de sobrevivência com cinco mapas.")
        assert (await core.messaging.receive(msg)).outbound
        assert await core.messaging.reflections.process_once()
        async def no_provider(*args, **kwargs):
            pytest.fail("authorized memory expression must not need a provider")
        monkeypatch.setattr(core.cognition, "respond", no_provider)
        private = (await core.messaging.receive(_dm(core, "natural-private",
                    "Amora, lembra do projeto Orquídea?"))).outbound[0]
        assert private.text.startswith("Lembro sim! Você me contou que")
        assert "O falante" not in private.text
        group = (await core.messaging.receive(_group(core, "natural-group"))).outbound[0]
        assert group.text == private.text
        assert group.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_AUTHORIZED"]
        assert group.metadata["memory_disclosure"]["source_excerpt"] == ORQ
        mid = group.metadata["memory_disclosure"]["record_id"]
        assert core.memory.get(mid).content == ORQ
        # A literal present only in the raw source must still block disclosure.
        monkeypatch.setenv("AMORA_CONFIDENTIAL_LITERALS_JSON", '["O falante"]')
        assert not core.messaging.store.disclosure_is_current(group)
        blocked = (await core.messaging.receive(_group(core, "natural-block"))).outbound[0]
        assert blocked.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_POLICY_BLOCKED"]
        assert "cinco mapas" not in blocked.text
        assert not blocked.metadata.get("memory_disclosure")
    finally:
        core.close()
