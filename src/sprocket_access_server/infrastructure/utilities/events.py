from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any


class InMemoryEventBus:
    def __init__(self) -> None:
        self._listeners: dict[type[Any], list[Callable[[Any], None]]] = defaultdict(list)

    def publish(self, event: Any) -> None:
        for event_type, listeners in tuple(self._listeners.items()):
            if isinstance(event, event_type):
                for listener in tuple(listeners):
                    listener(event)

    def subscribe(self, event_type: type[Any], listener: Callable[[Any], None]) -> None:
        self._listeners[event_type].append(listener)
