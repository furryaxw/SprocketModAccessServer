from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.core.contracts import ModuleContext, attach_modules, register_modules
from sprocket_access_server.infrastructure.database import SchemaRegistry, SQLiteDatabase
from sprocket_access_server.infrastructure.events import InfrastructureEventBus, ResourceChanged
from sprocket_access_server.infrastructure.network import ResourceRegistry
from sprocket_access_server.modules.registry import registered_modules
from sprocket_access_server.modules.system.platform import PlatformStore
from tests.support.module_context import registered_context


class MutablePermissions:
    permission_nodes: tuple[str, ...] = ()


class ModuleArchitectureTests(unittest.TestCase):
    def test_registered_modules_register_resources_through_one_registry(self) -> None:
        with TemporaryDirectory() as directory:
            context = registered_context(directory)
            modules = registered_modules()

        self.assertEqual([module.name for module in modules], [
            "system",
            "team",
            "users",
            "permission_templates",
            "permission_assignments",
            "packages",
            "keys",
            "audit",
            "applications",
        ])

        nodes = {definition.node for definition in context.resources.definitions()}
        self.assertIn("system.teams", nodes)
        self.assertIn("system.users", nodes)
        self.assertIn("team.system.users", nodes)
        self.assertTrue(all("<" not in node and ">" not in node for node in nodes))
        self.assertIn("system.teams.read", context.resources.permission_nodes())

    def test_business_module_source_does_not_use_raw_infrastructure_primitives(self) -> None:
        modules_root = Path(__file__).parents[1] / "src" / "sprocket_access_server" / "modules"
        prohibited = ("import sqlite3", "from starlette", "import os", "os.environ", "logging.basicConfig")
        for source in modules_root.rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            with self.subTest(source=source.name):
                self.assertFalse(any(value in text for value in prohibited))

    def test_flat_infrastructure_and_presentation_do_not_host_business_implementations(self) -> None:
        package_root = Path(__file__).parents[1] / "src" / "sprocket_access_server"
        flat_infrastructure = {
            path.name
            for path in (package_root / "infrastructure").glob("*.py")
        }
        presentation_files = {
            path.name
            for path in (package_root / "presentation").glob("*.py")
        }

        self.assertEqual(flat_infrastructure, {"__init__.py", "permissions.py"})
        self.assertEqual(
            presentation_files,
            {"__init__.py", "asgi.py", "authorization.py", "v1_http.py", "ws_hub.py"},
        )

    def test_resource_operations_derive_permission_nodes(self) -> None:
        resources = ResourceRegistry()

        def read(_data, _headers, _now):
            return {"ok": True}

        def download(_data, _headers, _now):
            return {"ok": True}

        resources.register("team.alpha.packages", description="Alpha Team packages") \
            .add_perm("read", read) \
            .add_perm("download", download)
        nodes = resources.permission_nodes()

        self.assertIn("team.alpha.packages.read", nodes)
        self.assertIn("team.alpha.packages.download", nodes)
        self.assertNotIn("team.alpha.packages.grant", nodes)

        resources.register("team.alpha.packages", auto_grant=True)
        self.assertIn("team.alpha.packages.grant", resources.permission_nodes())

    def test_public_operations_do_not_derive_permission_nodes(self) -> None:
        resources = ResourceRegistry()

        resources.register("system.authentication.github") \
            .add_public("exchange", lambda _data, _headers, _now: {"ok": True})
        resources.register("system.authentication.me") \
            .add_perm("read", lambda _data, _headers, _now: {"ok": True})

        nodes = set(resources.permission_nodes())
        self.assertNotIn("system.authentication.github.exchange", nodes)
        self.assertIn("system.authentication.me.read", nodes)

    def test_parent_read_permission_coexists_with_child_resources(self) -> None:
        resources = ResourceRegistry()

        with resources.module_scope():
            resources.register("aa.bb").add_perm("read", lambda _data, _headers, _now: {"ok": True})
            resources.register("aa.bb.cc").add_perm("start", lambda _data, _headers, _now: {"ok": True})

        nodes = set(resources.permission_nodes())
        self.assertIn("aa.bb.read", nodes)
        self.assertIn("aa.bb.grant", nodes)
        self.assertIn("aa.bb.cc.start", nodes)
        self.assertIn("aa.bb.cc.grant", nodes)

    def test_runtime_resource_registry_rejects_placeholder_nodes(self) -> None:
        resources = ResourceRegistry()

        with self.assertRaisesRegex(ValueError, "invalid"):
            resources.register("team.<team_id>.packages")

    def test_team_create_event_registers_module_owned_team_resources(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            context = ModuleContext(
                database=SchemaRegistry(database),
                events=InfrastructureEventBus(),
                resources=ResourceRegistry(),
                services={
                    "platform": PlatformStore(database, "simple"),
                    "permissions": MutablePermissions(),
                },
            )
            modules = register_modules(context, registered_modules())
            attach_modules(context, modules)

            context.events.publish(ResourceChanged(
                kind="create",
                node="team.alpha",
                action="manage",
                team_id="alpha",
            ))

        nodes = {definition.node for definition in context.resources.definitions()}
        permissions = set(context.services["permissions"].permission_nodes)
        self.assertIn("team.alpha", nodes)
        self.assertIn("team.alpha.keys", nodes)
        self.assertIn("team.alpha.packages", nodes)
        self.assertIn("team.alpha.package_uploads", nodes)
        self.assertIn("team.alpha.permission_templates", nodes)
        self.assertIn("team.alpha.permission_assignments", nodes)
        self.assertIn("team.alpha.package_uploads.grant", permissions)
        self.assertIn("team.alpha.keys.distribute", permissions)


if __name__ == "__main__":
    unittest.main()
