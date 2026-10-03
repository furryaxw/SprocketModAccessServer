from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..core.contracts import ModuleContext


@dataclass(frozen=True)
class ResourceManifestModule:
    """Business module registration seam owned by the core runtime."""

    name: str
    configure: Callable[[ModuleContext], None] | None = None
    attach_handlers: Callable[[ModuleContext], None] | None = None

    def register(self, context: ModuleContext) -> None:
        if self.configure is not None:
            self.configure(context)

    def attach(self, context: ModuleContext) -> None:
        if self.attach_handlers is not None:
            self.attach_handlers(context)
