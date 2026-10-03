from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol


class Event(Protocol):
    """Marker protocol for events crossing module boundaries."""


class EventBus(Protocol):
    def publish(self, event: Event) -> None: ...

    def subscribe(self, event_type: type[Event], listener: Callable[[Event], None]) -> None: ...


class ResourceRegistration(Protocol):
    @property
    def node(self) -> str: ...

    def add_perm(self, action: str, handler: Callable[..., Any]) -> "ResourceRegistration": ...

    def add_public(self, action: str, handler: Callable[..., Any]) -> "ResourceRegistration": ...


class ResourceRegistry(Protocol):
    def module_scope(self): ...

    def register(
            self,
            node: str,
            *,
            description: str = "",
            auto_grant: bool | None = None,
    ) -> ResourceRegistration: ...


class DatabaseRegistry(Protocol):
    def register_schema(self, module: str, schema: str) -> None: ...

    def initialize(self) -> None: ...


@dataclass
class ModuleContext:
    """Framework dependencies and registration points exposed to modules."""

    database: DatabaseRegistry
    events: EventBus
    resources: ResourceRegistry
    services: dict[str, Any] = field(default_factory=dict)

    def service(self, name: str) -> Any:
        try:
            return self.services[name]
        except KeyError as exc:
            raise RuntimeError(f"module dependency is not registered: {name}") from exc


class BusinessModule(Protocol):
    name: str

    def register(self, context: ModuleContext) -> None: ...

    def attach(self, context: ModuleContext) -> None: ...


def register_modules(context: ModuleContext, modules: Iterable[BusinessModule]) -> tuple[BusinessModule, ...]:
    registered: list[BusinessModule] = []
    for module in modules:
        if any(existing.name == module.name for existing in registered):
            raise ValueError(f"business module is already registered: {module.name}")
        with context.resources.module_scope():
            module.register(context)
        registered.append(module)
    context.database.initialize()
    return tuple(registered)


def attach_modules(context: ModuleContext, modules: Iterable[BusinessModule]) -> None:
    for module in modules:
        attach = getattr(module, "attach", None)
        if attach is not None:
            with context.resources.module_scope():
                attach(context)
