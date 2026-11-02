from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

if TYPE_CHECKING:
    from .capabilities import CapabilityRegistry


class RuntimeCapabilityBindings:
    """Per-Core bridge between the Action runtime and cognitive awareness.

    The binding is keyed by a runtime owner object (currently AutonomyEngine),
    not by a module-global singleton. Weak keys keep test/Core instances isolated
    and release bindings automatically when a Core is discarded.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._bindings: WeakKeyDictionary[object, CapabilityRegistry] = WeakKeyDictionary()

    def bind(self, owner: object, registry: CapabilityRegistry) -> None:
        with self._lock:
            self._bindings[owner] = registry

    def resolve(self, owner: object) -> CapabilityRegistry | None:
        with self._lock:
            return self._bindings.get(owner)

    def unbind(self, owner: object) -> None:
        with self._lock:
            self._bindings.pop(owner, None)


_RUNTIME_CAPABILITIES = RuntimeCapabilityBindings()


def bind_runtime_capabilities(owner: object, registry: CapabilityRegistry) -> None:
    _RUNTIME_CAPABILITIES.bind(owner, registry)


def resolve_runtime_capabilities(owner: object) -> CapabilityRegistry | None:
    return _RUNTIME_CAPABILITIES.resolve(owner)


def unbind_runtime_capabilities(owner: object) -> None:
    _RUNTIME_CAPABILITIES.unbind(owner)
