from __future__ import annotations

from amora.app import AmoraCore
from amora.messaging import (
    AttentionDecision,
    ChannelType,
    ExpressionKind,
    ExpressionRef,
    ExternalActorRef,
    InboundMessage,
    MessagingPlatform,
    RoutingAction,
)
from amora.time import SimulatedClock


def _sticker(sticker_id: str, name: str, *tags: str) -> ExpressionRef:
    return ExpressionRef(
        kind=ExpressionKind.STICKER,
        native_id=sticker_id,
        name=name,
        tags=list(tags),
        format_type=1,
        guild_or_group_id="guild-1",
    )


def _message(
    core: AmoraCore,
    text: str = "Amora, manda essa figurinha de volta",
    *,
    source_sticker: bool = True,
    catalog: list[ExpressionRef] | None = None,
) -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id="channel-sticker",
        message_id="message-sticker",
        native_message_id="message-sticker",
        channel_type=ChannelType.GROUP,
        sender_identifiers=[
            ExternalActorRef(
                identifier_type="discord_user_id",
                external_actor_id="111111111111111111",
            )
        ],
        text=text,
        mentions_amora=True,
        expressions=[_sticker("sticker-1", "morre", "laugh", "lol")] if source_sticker else [],
        available_expressions=catalog or [],
        timestamp=core.clock.now(),
        guild_or_group_id="guild-1",
    )


def _decision() -> AttentionDecision:
    return AttentionDecision(
        action=RoutingAction.RESPOND,
        score=1.0,
        speaker_actor_ref="discord:111111111111111111",
        addressee_id="ENTITY_AMORA",
        reason_codes=["TEST_DIRECT_ATTENTION"],
    )


def test_explicit_sticker_reuse_persists_as_expression_only_outbound(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        message = _message(core)
        outbound = core.messaging._queue_outbound(
            message,
            _decision(),
            "texto que deve ser substituído pela expressão nativa",
            metadata={"transport_revision": 1},
            reason_codes=["TEST"],
        )

        assert outbound.text == ""
        assert len(outbound.expressions) == 1
        assert outbound.expressions[0].native_id == "sticker-1"
        assert outbound.metadata["expression_only"] is True
        assert outbound.metadata["expression_selection_source"] == "source_message"
        assert outbound.metadata["transport_expressions"][0]["native_id"] == "sticker-1"

        claimed = core.messaging_store.claim_outbound(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            limit=1,
            lease_seconds=30,
        )
        assert len(claimed) == 1
        assert claimed[0].text == ""
        assert claimed[0].metadata["expression_only"] is True
        assert claimed[0].metadata["transport_expressions"][0]["native_id"] == "sticker-1"
    finally:
        core.close()


def test_sticker_reuse_intent_is_explicit_and_negative_safe(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        service = core.messaging
        assert service._requested_sticker_reuse(_message(core)) is not None
        assert service._requested_sticker_reuse(
            _message(core, "Amora, vc consegue enviar figurinhas daqui do grupo? Tipo essa")
        ) is not None
        assert service._requested_sticker_reuse(_message(core, "Amora, não manda essa figurinha")) is None
        assert service._requested_sticker_reuse(_message(core, "Amora, essa figurinha é muito boa")) is None
        assert service._requested_sticker_reuse(_message(core, "Amora, manda a configuração")) is None
    finally:
        core.close()


def test_catalog_selection_prefers_semantic_match_and_never_provider_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        catalog = [
            _sticker("happy-1", "happy", "smile", "happy"),
            _sticker("angry-1", "rage", "raiva", "angry"),
            _sticker("sad-1", "cry", "triste", "sad"),
        ]
        message = _message(
            core,
            "Amora, manda uma figurinha de raiva",
            source_sticker=False,
            catalog=catalog,
        )
        selection = core.messaging._requested_sticker_expression(message, "eita, fiquei brava com isso")
        assert selection is not None
        expression, source = selection
        assert source == "guild_catalog"
        assert expression.native_id == "angry-1"

        outbound = core.messaging._queue_outbound(
            message,
            _decision(),
            "eita, fiquei brava com isso",
            metadata={"provider": "groq", "transport_revision": 1},
            reason_codes=["TEST"],
        )
        assert outbound.text == ""
        assert outbound.expressions[0].native_id == "angry-1"
        assert outbound.metadata["expression_selection_source"] == "guild_catalog"
    finally:
        core.close()


def test_catalog_does_not_hijack_non_sticker_requests(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        catalog = [_sticker("one", "one"), _sticker("two", "two")]
        for text in ("Amora, manda uma foto", "Amora, manda essa foto", "Amora, envia um arquivo"):
            message = _message(core, text, source_sticker=False, catalog=catalog)
            assert core.messaging._requested_sticker_expression(message, "beleza") is None
    finally:
        core.close()


def test_generic_catalog_selection_is_deterministic_for_retries(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        catalog = [
            _sticker("one", "one"),
            _sticker("two", "two"),
            _sticker("three", "three"),
        ]
        message = _message(core, "Amora, manda uma figurinha", source_sticker=False, catalog=catalog)
        first = core.messaging._requested_sticker_expression(message, "beleza")
        second = core.messaging._requested_sticker_expression(message, "beleza")
        assert first is not None and second is not None
        assert first[0].native_id == second[0].native_id
    finally:
        core.close()
