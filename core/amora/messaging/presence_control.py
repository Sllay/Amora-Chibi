from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from amora.identity import PersonProfileService

from .models import MessagingPlatform, PresenceCommand, PresenceSnapshot
from .presence import PlatformPresenceController


class PresenceControlAction(StrEnum):
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    CHECK = "check"
    STATUS = "status"


@dataclass(frozen=True, slots=True)
class PresenceControlIntent:
    platform: MessagingPlatform
    action: PresenceControlAction


@dataclass(frozen=True, slots=True)
class PresenceControlOutcome:
    intent: PresenceControlIntent
    recognized: bool
    authorized: bool
    executed: bool
    response_text: str
    snapshot: PresenceSnapshot | None = None


_PLATFORM_PATTERNS: tuple[tuple[MessagingPlatform, re.Pattern[str]], ...] = (
    (MessagingPlatform.DISCORD, re.compile(r"\bdiscord\b", re.IGNORECASE)),
    (
        MessagingPlatform.WHATSAPP,
        re.compile(r"\b(?:whats\s*app|whatsapp|zap(?:zap)?)\b", re.IGNORECASE),
    ),
)

_ACTION_PATTERNS: tuple[tuple[PresenceControlAction, re.Pattern[str]], ...] = (
    (
        PresenceControlAction.DISCONNECT,
        re.compile(
            r"\b(?:desconect(?:a|e|ar)|fica\s+offline|sai\s+do|saia\s+do|desliga(?:r)?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        PresenceControlAction.CONNECT,
        re.compile(
            r"\b(?:conect(?:a|e|ar)|fica\s+online|fique\s+online|acorda\s+no|acorde\s+no|liga(?:r)?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        PresenceControlAction.CHECK,
        re.compile(
            r"\b(?:chec(?:a|ar|que)|verific(?:a|ar|que)|confere|conferir|olha\s+(?:o|no)|d[áa]\s+uma\s+olhada)\b",
            re.IGNORECASE,
        ),
    ),
    (
        PresenceControlAction.STATUS,
        re.compile(
            r"\b(?:status|estado|t[áa]\s+(?:online|conectad[ao])|est[áa]\s+(?:online|conectad[ao]))\b",
            re.IGNORECASE,
        ),
    ),
)

_CALL_WORDS = re.compile(r"\b(?:call|chamada|voz|voice|canal\s+de\s+voz)\b", re.IGNORECASE)


class PlatformPresenceControlService:
    """Authorize and execute explicit platform-presence commands in Core.

    Stable platform identity is resolved before this service is called. The
    transport worker never receives authority to decide who may connect or
    disconnect Amora. Voice/call phrases are deliberately excluded because text
    transport presence and Discord voice presence are separate subsystems.
    """

    def __init__(
        self,
        *,
        profiles: PersonProfileService,
        presence: PlatformPresenceController,
    ) -> None:
        self.profiles = profiles
        self.presence = presence

    @staticmethod
    def parse(text: str) -> PresenceControlIntent | None:
        value = " ".join(str(text or "").strip().split())
        if not value or _CALL_WORDS.search(value):
            return None

        platform = next(
            (candidate for candidate, pattern in _PLATFORM_PATTERNS if pattern.search(value)),
            None,
        )
        if platform is None:
            return None

        action = next(
            (candidate for candidate, pattern in _ACTION_PATTERNS if pattern.search(value)),
            None,
        )
        if action is None:
            return None
        return PresenceControlIntent(platform=platform, action=action)

    def handle(
        self,
        text: str,
        *,
        speaker_entity_id: str | None,
        account_scope: str = "primary",
    ) -> PresenceControlOutcome | None:
        intent = self.parse(text)
        if intent is None:
            return None
        return self.execute_intent(
            intent,
            speaker_entity_id=speaker_entity_id,
            account_scope=account_scope,
        )

    def execute_intent(
        self,
        intent: PresenceControlIntent,
        *,
        speaker_entity_id: str | None,
        account_scope: str = "primary",
    ) -> PresenceControlOutcome:
        if not self.profiles.can_control_platform_presence(speaker_entity_id):
            return PresenceControlOutcome(
                intent=intent,
                recognized=True,
                authorized=False,
                executed=False,
                response_text="Não posso mudar nem consultar minha presença nessa plataforma por esse pedido.",
            )

        if intent.action == PresenceControlAction.STATUS:
            snapshot = self.presence.snapshot(intent.platform, account_scope)
            return PresenceControlOutcome(
                intent=intent,
                recognized=True,
                authorized=True,
                executed=False,
                snapshot=snapshot,
                response_text=self._status_text(snapshot),
            )

        command = {
            PresenceControlAction.CONNECT: PresenceCommand.CONNECT,
            PresenceControlAction.DISCONNECT: PresenceCommand.DISCONNECT,
            PresenceControlAction.CHECK: PresenceCommand.CHECK,
        }[intent.action]
        snapshot = self.presence.request(
            intent.platform,
            command,
            account_scope=account_scope,
        )
        return PresenceControlOutcome(
            intent=intent,
            recognized=True,
            authorized=True,
            executed=True,
            snapshot=snapshot,
            response_text=self._command_text(intent, snapshot),
        )

    @staticmethod
    def _platform_name(platform: MessagingPlatform) -> str:
        return "Discord" if platform == MessagingPlatform.DISCORD else "WhatsApp"

    @classmethod
    def _status_text(cls, snapshot: PresenceSnapshot) -> str:
        platform = cls._platform_name(snapshot.platform)
        if snapshot.state.value == "connected":
            return f"Tô conectada no {platform}."
        if snapshot.desired_connected:
            return f"Quero ficar conectada no {platform}, mas o transporte agora está {snapshot.state.value}."
        if snapshot.check_requested:
            return f"Tenho uma checagem do {platform} pendente."
        return f"Tô desconectada do {platform}."

    @classmethod
    def _command_text(
        cls,
        intent: PresenceControlIntent,
        snapshot: PresenceSnapshot,
    ) -> str:
        platform = cls._platform_name(intent.platform)
        if intent.action == PresenceControlAction.CONNECT:
            if snapshot.state.value == "connected":
                return f"Já tô conectada no {platform}."
            return f"Certo. Vou conectar no {platform}."
        if intent.action == PresenceControlAction.DISCONNECT:
            if snapshot.state.value == "disconnected":
                return f"Certo. Vou continuar desconectada do {platform}."
            return f"Certo. Vou desconectar do {platform}."
        return f"Certo. Vou checar o {platform}."
