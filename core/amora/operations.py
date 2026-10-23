"""Safe operational state for Living Console / future Dev MCP.

Bounded ephemeral projections, not cognitive memory or private model reasoning.
Persisted journals/action results remain the separate forensic source of truth.
"""

from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
import hashlib
from threading import RLock
from uuid import UUID


@dataclass
class ErrorGroup:
    fingerprint: str
    module: str
    error_code: str
    occurrence_count: int
    first_seen: str
    last_seen: str
    correlation_id: str | None


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


class OperationMonitor:
    def __init__(self, clock, *, error_capacity=128, event_capacity=256):
        self.clock = clock
        self._errors = OrderedDict()
        self._events = deque(maxlen=event_capacity)
        self._capacity = error_capacity
        self._lock = RLock()

    def error(self, module, error_code, *, correlation_id=None):
        # Callers supply stable machine codes, never exception messages/bodies.
        fingerprint = hashlib.sha256(f"{module}:{error_code}".encode()).hexdigest()[:24]
        now = self.clock.now().isoformat()
        with self._lock:
            group = self._errors.get(fingerprint)
            if group is None:
                group = ErrorGroup(fingerprint, module, error_code, 0, now, now, None)
                self._errors[fingerprint] = group
            group.occurrence_count += 1
            group.last_seen = now
            group.correlation_id = identifier(correlation_id)
            self._errors.move_to_end(fingerprint)
            while len(self._errors) > self._capacity:
                self._errors.popitem(last=False)
        return fingerprint

    def action(self, call, state, *, error_code=None, latency_ms=None):
        with self._lock:
            self._events.append(
                {
                    "kind": "action",
                    "timestamp": self.clock.now().isoformat(),
                    "state": state,
                    "correlation_id": identifier(call.id),
                    "plan_id": identifier(call.plan_id),
                    "error_code": error_code,
                    "latency_ms": latency_ms,
                }
            )
        if error_code:
            self.error("action", error_code, correlation_id=call.id)

    def snapshot(self):
        with self._lock:
            return {
                "errors": [asdict(group) for group in self._errors.values()],
                "events": list(self._events),
                "retention": "bounded-process-local",
            }


def operational_snapshot(core, voice=None):
    """Explicit projection: never return an entire health/journal/context payload."""
    from amora.events import PrivacyScope

    intention = core.autonomy.store.current_intention()
    journals = core.cognition.store.recent_journal(limit=30)
    safe = [
        row
        for row in journals
        if row.privacy_scope not in {PrivacyScope.AUTH, PrivacyScope.SECRET}
    ]
    latest = safe[-1] if safe else None
    dialogue_act = None
    for row in reversed(safe):
        plan = row.payload.get("dialogue_plan")
        if isinstance(plan, dict) and isinstance(plan.get("act"), str):
            from amora.cognition.dialogue import DialogueAct

            try:
                dialogue_act = DialogueAct(plan["act"]).value
            except ValueError:
                pass
            break
    providers = []
    for descriptor in core.models.descriptors():
        providers.append(
            {
                "provider": descriptor.name,
                "model": descriptor.model,
                "available": descriptor.available,
            }
        )
    # Usage ledger contains counters/quota only, not prompts or model reasoning.
    usage = core.cognition.store.usage_ledger()
    turns = core.conversation.store.list_utterances(limit=30)
    speech = {}
    for item in turns:
        if item.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            continue
        speech[item.status.value] = speech.get(item.status.value, 0) + 1
    voice_state = voice.state() if voice else {}
    cooldowns = getattr(core.cognition, "_provider_cooldowns", {})
    return {
        "schema": "amora.operations.v1",
        "health": core.readiness(),
        "current_intention": {
            "id": str(intention.id),
            "kind": intention.kind.value,
            "status": intention.status.value,
        }
        if intention
        else None,
        "dialogue_act": dialogue_act,
        "task": latest.stage.value if latest else None,
        "turn_id": identifier(latest.turn_id) if latest else None,
        "correlation_id": identifier(latest.correlation_id) if latest else None,
        "provider": latest.provider if latest else None,
        "model": latest.model if latest else None,
        "latency_ms": latest.duration_ms if latest else None,
        "providers": providers,
        "quota": usage,
        "cooldowns": {
            key: value.isoformat()
            for key, value in cooldowns.items()
            if value > core.clock.now()
        },
        "modules": {
            "core": "OFF" if core._closed else "ON",
            "storage": core.database.backend.capabilities.name,
            "voice": "ON" if voice_state.get("available") else "DEGRADED",
        },
        "speech": {
            "recent_status_counts": speech,
            "pending": core.conversation.store.pending_count(),
        },
        "jobs": core.scheduler.snapshot(),
        **core.operations.snapshot(),
    }
