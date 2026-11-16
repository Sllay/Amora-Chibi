"""AGENDA.1: real Core storage, scheduler and native Discord queue."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from amora.agenda import AgendaCreate, AgendaService
from amora.agenda.intent import parse_agenda_text
from amora.agenda.service import agenda_claim_allowed
from amora.api import create_api
from amora.app import AmoraCore
from amora.messaging import ChannelType, MessagingPlatform, PresenceCommand, PresenceState
from amora.time import SimulatedClock
from test_platform_messaging_step2 import _bind_bk, _message
from remote_storage_contracts_v13_source import remote

START = datetime(2026, 11, 16, 18, 20, tzinfo=timezone.utc)
EXTERNAL = "111111111111111111"


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock(START)
    value = AmoraCore.bootstrap(clock=clock)
    _bind_bk(value)
    yield value
    value.close()


def agenda(core):
    return AgendaService(core.database, core.clock, core.messaging_store)


def target():
    return {"account_scope": "primary", "conversation_id": "agenda-dm",
            "external_actor_id": EXTERNAL}


def connected(core):
    p = core.platform_presence.request(MessagingPlatform.DISCORD, PresenceCommand.CONNECT)
    core.platform_presence.report(MessagingPlatform.DISCORD, PresenceState.CONNECTING, generation=p.generation)
    core.platform_presence.report(MessagingPlatform.DISCORD, PresenceState.CONNECTED, generation=p.generation)


async def seed(core, text="Oi Amora", mid="agenda-hello"):
    result = await core.messaging.receive(_message(
        core, message_id=mid, actor_id=EXTERNAL, channel_type=ChannelType.DM,
        conversation_id="agenda-dm", text=text,
    ))
    assert result.outbound
    # Clear the ordinary reply before verifying scheduled outbound delivery.
    claims = core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary")
    if claims:
        for msg in claims:
            assert core.messaging_store.ack_outbound(msg.id, sent=True, external_message_id="reply-" + str(msg.id))
    return result


@pytest.mark.asyncio
async def test_2030_schedule_survives_restart_and_fires_at_actual_due(core):
    await seed(core)
    service = agenda(core)
    due = datetime(2030, 11, 11, 15, 30, tzinfo=timezone(timedelta(hours=-3)))
    item = service.create(AgendaCreate(text="Ver se ainda estou por aqui", due_at=due),
                          target=target())
    assert len(service.list(year=2030)) == 1
    assert service.list(year=2026) == []
    assert service.tick() == []
    home = core.paths.home if hasattr(core.paths, "home") else None
    core.clock.advance(timedelta(days=1456))
    # An explicit missed occurrence must not flood the DM on a late restart.
    assert service.tick() == []
    assert service.list(year=2030)[0].id == item.id


@pytest.mark.asyncio
async def test_native_command_and_single_exact_occurrence(core):
    connected(core)
    result = await seed(core, "Amora me lembra hoje às 15:30 de cobrar meu amigo pela camisa")
    assert "agendado" in result.outbound[0].text
    service = agenda(core)
    items = service.list()
    assert len(items) == 1
    assert items[0].due_at == datetime(2026, 11, 16, 18, 30, tzinfo=timezone.utc)
    assert await core.messaging.receive(_message(
        core, message_id="agenda-hello", actor_id=EXTERNAL, channel_type=ChannelType.DM,
        conversation_id="agenda-dm", text="Amora me lembra hoje às 15:30 de cobrar meu amigo pela camisa",
    ))
    assert len(service.list()) == 1
    core.clock.advance(timedelta(minutes=10))
    sent = service.tick()
    assert len(sent) == 1
    assert "cobrar meu amigo" in sent[0].text
    assert service.tick() == []
    claim = core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary")
    assert [x.id for x in claim] == [sent[0].id]
    assert core.messaging_store.ack_outbound(sent[0].id, sent=True, external_message_id="discord-agenda-1")
    assert service.tick() == []


@pytest.mark.asyncio
async def test_repeat_ten_minutes_until_end_then_cancel(core):
    connected(core)
    await seed(core)
    service = agenda(core)
    start = datetime(2026, 11, 16, 18, 30, tzinfo=timezone.utc)
    stop = datetime(2026, 11, 17, 2, 59, 59, tzinfo=timezone.utc)
    item = service.create(AgendaCreate(text="Respiração automática desativada", due_at=start,
                                      interval_minutes=10, until_at=stop), target=target())
    core.clock.advance(timedelta(minutes=10))
    first = service.tick()
    assert len(first) == 1
    claims = core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary")
    assert len(claims) == 1
    core.messaging_store.ack_outbound(first[0].id, sent=True, external_message_id="test")
    core.clock.advance(timedelta(minutes=10))
    second = service.tick()
    assert len(second) == 1 and second[0].id != first[0].id
    cancelled = service.cancel(item.id)
    assert cancelled.status == "cancelled"
    assert service.tick() == []
    assert core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary") == ()
    assert len(service.list(year=2026)) == 1


@pytest.mark.asyncio
async def test_revoked_binding_prevents_worker_claim(core):
    connected(core)
    await seed(core)
    service = agenda(core)
    due = core.clock.now() + timedelta(minutes=10)
    service.create(AgendaCreate(text="Segredo privado", due_at=due), target=target())
    core.clock.advance(timedelta(minutes=10))
    [outbound] = service.tick()
    with core.database._lock, core.database._conn:
        core.database._conn.execute("UPDATE platform_identity_bindings SET enabled=0 WHERE external_actor_id=?", (EXTERNAL,))
    assert core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary") == ()
    assert not agenda_claim_allowed(core.database._conn, outbound, core.clock.now())


@pytest.mark.asyncio
async def test_operator_scopes_and_ambiguous_year_cancel(core):
    await seed(core)
    service = agenda(core)
    one = service.create(AgendaCreate(text="Um compromisso", due_at=datetime(2030, 11, 11, 18, tzinfo=timezone.utc)), target=target())
    two = service.create(AgendaCreate(text="Outro compromisso", due_at=datetime(2030, 12, 11, 18, tzinfo=timezone.utc)), target=target())
    intent = parse_agenda_text("Amora, aquela agenda de 2030 não precisa avisar", core.clock.now())
    assert intent.kind == "cancel_search" and intent.year == 2030
    assert len(service.list(year=intent.year, include_cancelled=False)) == 2
    assert len(service.list()) == 2
    service.cancel(one.id)
    assert service.list(year=2030)[0].status == "cancelled"
    assert service.list(year=2030)[1].status == "active"
    assert service.cancel(one.id).status == "cancelled"
    with TestClient(create_api(core)) as client:
        viewer = client.get("/v1/operator/agenda")
        assert viewer.status_code in (401, 403)
        admin = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
        result = client.get("/v1/operator/agenda?year=2030", headers=admin)
        assert result.status_code == 200, result.text
        assert len(result.json()["items"]) == 2
        assert "external_actor_id" not in result.text
        assert client.delete("/v1/operator/agenda/" + str(two.id), headers=admin).status_code == 200
        assert service.list(year=2030)[1].status == "cancelled"


@pytest.mark.parametrize("text,expected", [
    ("Amora me lembra hoje às 15:30 de cobrar meu amigo", "create"),
    ("Amora em 11/11/2030 ver se estou vivo", "need_time"),
    ("Amora falar pra mim a cada 10 minutos 'frase engraçada' o dia todo", "create"),
    ("Amora, aquela agenda de 2030 não precisa avisar", "cancel_search"),
    ("Amora, quais agendas tenho em 2030?", "list"),
])
def test_portuguese_intent(text, expected):
    intent = parse_agenda_text(text, START)
    assert intent is not None and intent.kind == expected
    if "cada 10" in text:
        assert intent.interval_minutes == 10
        assert intent.until_at > intent.due_at


def test_past_date_and_missing_end_denied(core):
    service = agenda(core)
    with pytest.raises(ValueError, match="AGENDA_DATE_OUT_OF_RANGE"):
        service.create(AgendaCreate(text="muito tarde", due_at=START - timedelta(hours=1)))
    with pytest.raises(ValueError, match="AGENDA_INTERVAL_END_REQUIRED"):
        AgendaCreate(text="intervalo", due_at=START+timedelta(minutes=10), interval_minutes=10)


@pytest.mark.asyncio
async def test_2030_occurrence_fires_after_core_restart(tmp_path, monkeypatch):
    home = tmp_path / "agenda-restart"
    monkeypatch.setenv("AMORA_HOME", str(home))
    clock = SimulatedClock(START)
    first = AmoraCore.bootstrap(clock=clock)
    try:
        _bind_bk(first)
        await seed(first)
        item = AgendaService(first.database, clock, first.messaging_store).create(
            AgendaCreate(text="Ver se estou vivo em 2030",
                         due_at=datetime(2030, 11, 11, 18, 30, tzinfo=timezone.utc)),
            target=target(),
        )
    finally:
        first.close()
    second = AmoraCore.bootstrap(clock=clock)
    try:
        assert AgendaService(second.database, clock, second.messaging_store).list(year=2030)[0].id == item.id
        connected(second)
        deadline = item.due_at
        clock.advance(deadline - clock.now())
        service = AgendaService(second.database, clock, second.messaging_store)
        [scheduled] = service.tick()
        assert scheduled.text == "Ver se estou vivo em 2030"
        assert service.tick() == []
        [claim] = second.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary")
        assert claim.id == scheduled.id
    finally:
        second.close()


@pytest.mark.asyncio
async def test_emergency_disable_leaves_schedule_intact(core, monkeypatch):
    connected(core)
    await seed(core)
    service = agenda(core)
    service.create(AgendaCreate(text="Não esquecer", due_at=core.clock.now()+timedelta(minutes=10)),
                   target=target())
    core.clock.advance(timedelta(minutes=10))
    monkeypatch.setenv("AMORA_AGENDA_ENABLED", "0")
    assert service.tick() == []
    assert len(service.list()) == 1
    monkeypatch.setenv("AMORA_AGENDA_ENABLED", "1")
    assert len(service.tick()) == 1


def test_invalid_unaware_and_ambiguous_date(core):
    with pytest.raises(ValueError, match="AGENDA_TIMEZONE_REQUIRED"):
        AgendaCreate(text="Errado", due_at=datetime(2030,11,11,12,0))
    assert parse_agenda_text("Amora em 11/11/2030 ver se estou vivo", START).kind == "need_time"
    assert parse_agenda_text("Amora, a cada 1 minuto fala 'oi' o dia todo", START).kind == "invalid_interval"


@pytest.mark.asyncio
async def test_hrana_remote_agenda_persists_and_delivers(remote, tmp_path, monkeypatch):
    backend, server = remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "agenda-remote"))
    clock = SimulatedClock(START)
    core = AmoraCore.bootstrap(storage=backend, clock=clock)
    try:
        _bind_bk(core)
        connected(core)
        await seed(core)
        service = agenda(core)
        item = service.create(AgendaCreate(text="Lembrete remoto seguro",
                                          due_at=clock.now() + timedelta(minutes=10)),
                              target=target())
        assert service.list()[0].id == item.id
        clock.advance(timedelta(minutes=10))
        [message] = service.tick()
        assert message.text == "Lembrete remoto seguro"
        claims = core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary")
        assert [x.id for x in claims] == [message.id]
        assert core.messaging_store.ack_outbound(message.id, sent=True, external_message_id="remote-ack")
        assert service.cancel(item.id).status == "cancelled"
        assert service.list()[0].status == "cancelled"
    finally:
        core.close()
    assert not server.streams


def test_operator_viewer_role_cannot_read_private_agendas(core):
    from amora.security import issue_browser_token
    token = issue_browser_token(core.secrets.get("LOCAL_API_TOKEN"), core.clock, "web")
    with TestClient(create_api(core)) as client:
        response = client.get("/v1/operator/agenda",
                              headers={"Authorization": "Bearer " + token})
        assert response.status_code == 403
