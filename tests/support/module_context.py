from __future__ import annotations

from pathlib import Path

from sprocket_access_server.core.contracts import ModuleContext, attach_modules, register_modules
from sprocket_access_server.infrastructure.database import SchemaRegistry, SQLiteDatabase
from sprocket_access_server.infrastructure.events import InfrastructureEventBus
from sprocket_access_server.infrastructure.network import ResourceRegistry
from sprocket_access_server.modules.registry import registered_modules


def registered_context(directory: str | Path) -> ModuleContext:
    database = SQLiteDatabase(Path(directory) / "access.db")
    context = ModuleContext(
        database=SchemaRegistry(database),
        events=InfrastructureEventBus(),
        resources=ResourceRegistry(),
    )
    modules = register_modules(context, registered_modules())
    attach_modules(context, modules)
    return context
