from __future__ import annotations

from amora.time import Clock

from .models import MessagingPlatform, PresenceCommand, PresenceSnapshot, PresenceState
from .store import MessagingStore


_ALLOWED_REPORTS: dict[PresenceState, set[PresenceState]] = {
    PresenceState.DISCONNECTED: {PresenceState.DISCONNECTED, PresenceState.CONNECTING, PresenceState.ERROR},
    PresenceState.CONNECTING: {PresenceState.CONNECTING, PresenceState.CONNECTED, PresenceState.DISCONNECTED, PresenceState.ERROR},
    PresenceState.CONNECTED: {PresenceState.CONNECTED, PresenceState.CHECKING, PresenceState.DISCONNECTING, PresenceState.DISCONNECTED, PresenceState.ERROR},
    PresenceState.CHECKING: {PresenceState.CHECKING, PresenceState.CONNECTED, PresenceState.DISCONNECTING, PresenceState.DISCONNECTED, PresenceState.ERROR},
    PresenceState.DISCONNECTING: {PresenceState.DISCONNECTING, PresenceState.DISCONNECTED, PresenceState.ERROR},
    PresenceState.ERROR: {PresenceState.ERROR, PresenceState.DISCONNECTED, PresenceState.CONNECTING},
}


class PlatformPresenceController:
    """Core-owned intent for platform connectivity, reported by isolated workers.

    A persisted `desired_connected` survives compute replacement. The physical
    transport state does not: the primary bootstrap controller resets transient
    worker-reported state to DISCONNECTED so Amora never pretends a socket
    survived a process restart. Secondary policy facades may opt out of repeating
    that startup reset while sharing the same canonical store.
    """

    def __init__(
        self,
        store: MessagingStore,
        clock: Clock,
        *,
        reset_transient: bool = True,
    ) -> None:
        self.store = store
        self.clock = clock
        if reset_transient:
            self._reset_transient_states()

    def _reset_transient_states(self) -> None:
        now = self.clock.now()
        for current in self.store.list_presence():
            if current.state == PresenceState.DISCONNECTED:
                continue
            self.store.put_presence(
                current.model_copy(
                    update={
                        "state": PresenceState.DISCONNECTED,
                        "updated_at": now,
                        "last_disconnected_at": now,
                        "last_error_code": "CORE_RESTART_TRANSPORT_UNKNOWN",
                        "generation": current.generation + 1,
                    }
                )
            )

    def snapshot(
        self,
        platform: MessagingPlatform,
        account_scope: str = "primary",
    ) -> PresenceSnapshot:
        current = self.store.get_presence(platform, account_scope)
        if current is not None:
            return current
        snapshot = PresenceSnapshot(
            platform=platform,
            account_scope=account_scope,
            state=PresenceState.DISCONNECTED,
            updated_at=self.clock.now(),
        )
        return self.store.put_presence(snapshot)

    def request(
        self,
        platform: MessagingPlatform,
        command: PresenceCommand,
        *,
        account_scope: str = "primary",
    ) -> PresenceSnapshot:
        current = self.snapshot(platform, account_scope)
        now = self.clock.now()
        update: dict[str, object] = {
            "updated_at": now,
            "generation": current.generation + 1,
            "last_error_code": None,
        }
        if command == PresenceCommand.CONNECT:
            update["desired_connected"] = True
        elif command == PresenceCommand.DISCONNECT:
            update["desired_connected"] = False
            update["check_requested"] = False
        elif command == PresenceCommand.CHECK:
            update["check_requested"] = True
        else:  # pragma: no cover - enum is closed
            raise ValueError(f"unsupported presence command: {command}")
        return self.store.put_presence(current.model_copy(update=update))

    def report(
        self,
        platform: MessagingPlatform,
        state: PresenceState,
        *,
        account_scope: str = "primary",
        generation: int | None = None,
        error_code: str | None = None,
        check_complete: bool = False,
    ) -> PresenceSnapshot:
        current = self.snapshot(platform, account_scope)
        if generation is not None and generation < current.generation:
            raise ValueError("stale presence generation")
        if state not in _ALLOWED_REPORTS[current.state]:
            raise ValueError(f"invalid presence transition: {current.state.value}->{state.value}")
        now = self.clock.now()
        update: dict[str, object] = {
            "state": state,
            "updated_at": now,
            "generation": max(current.generation, generation or current.generation),
            "last_error_code": error_code[:120] if error_code else None,
        }
        if state == PresenceState.CONNECTED:
            update["last_connected_at"] = now
        if state == PresenceState.DISCONNECTED:
            update["last_disconnected_at"] = now
        if state == PresenceState.ERROR and not error_code:
            update["last_error_code"] = "WORKER_REPORTED_ERROR"
        if check_complete:
            update["check_requested"] = False
        return self.store.put_presence(current.model_copy(update=update))

    def state(self) -> dict[str, object]:
        items = self.store.list_presence()
        return {
            "platform_presence": [
                {
                    "platform": item.platform.value,
                    "account_scope": item.account_scope,
                    "state": item.state.value,
                    "desired_connected": item.desired_connected,
                    "check_requested": item.check_requested,
                    "generation": item.generation,
                    "last_error_code": item.last_error_code,
                }
                for item in items
            ]
        }
