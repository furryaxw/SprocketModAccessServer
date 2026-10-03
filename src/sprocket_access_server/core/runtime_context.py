from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import BusinessModule, ModuleContext


@dataclass
class ServiceRuntime:
    """Lifecycle owner for one fully composed Access Server."""

    context: ModuleContext
    modules: tuple[BusinessModule, ...]
    app: Any

    async def startup(self) -> None:
        startup = getattr(self.app, "startup", None)
        if startup is not None:
            result = startup()
            if hasattr(result, "__await__"):
                await result

    async def shutdown(self) -> None:
        shutdown = getattr(self.app, "shutdown", None)
        if shutdown is not None:
            result = shutdown()
            if hasattr(result, "__await__"):
                await result
