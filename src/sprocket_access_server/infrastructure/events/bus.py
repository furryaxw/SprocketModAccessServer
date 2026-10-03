from __future__ import annotations

from ..utilities.events import InMemoryEventBus


class InfrastructureEventBus(InMemoryEventBus):
    """Concrete event bus supplied to business modules."""
