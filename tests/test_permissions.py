from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.network import ResourceRegistry
from sprocket_access_server.infrastructure.permissions import PermissionCatalog
from tests.support.module_context import registered_context


def registered_resource_permissions(directory: str):
    context = registered_context(directory)
    return context.resources.permission_nodes()


class PermissionCatalogTests(unittest.TestCase):
    def test_validation_normalizes_and_deduplicates_permissions(self) -> None:
        result = PermissionCatalog.validate([" team.default.packages.example_mod.download ", "team.default.packages.example_mod.download", "team.default.custom.beta.read"])
        self.assertEqual(result, frozenset({"team.default.packages.example_mod.download", "team.default.custom.beta.read"}))

    def test_validation_rejects_unknown_or_malformed_namespaces(self) -> None:
        for value in ("unknown:value", "download:", "team.default.packages.bad.download/value"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid"):
                PermissionCatalog.validate([value])

    def test_validation_accepts_dot_system_permissions(self) -> None:
        result = PermissionCatalog.validate(["team.system.users.read", "system.teams.manage"])
        self.assertEqual(result, frozenset({"team.system.users.read", "system.teams.manage"}))

    def test_module_resources_keep_explicit_read_and_receive_automatic_grant(self) -> None:
        with TemporaryDirectory() as directory:
            resource_permissions = registered_resource_permissions(directory)
        nodes = set(resource_permissions)
        for branch in ("system.teams", "system.users", "team.system.users"):
            with self.subTest(branch=branch):
                self.assertIn(f"{branch}.grant", nodes)

        for branch in (
            "system.authentication.github",
            "system.authentication.github.device",
            "system.authentication.github.web",
            "system.keys.redeem",
        ):
            with self.subTest(branch=branch):
                self.assertNotIn(f"{branch}.read", nodes)
                if branch.startswith("system.authentication.github"):
                    self.assertNotIn(f"{branch}.grant", nodes)

    def test_manual_resources_keep_only_explicit_actions(self) -> None:
        with TemporaryDirectory() as directory:
            resources = ResourceRegistry()
            resources.register("team.alpha.permission_assignments").add_perm(
                "manage", lambda _data, _headers, _now: {"ok": True}
            )
            resources.register("team.alpha.keys").add_perm(
                "distribute", lambda _data, _headers, _now: {"ok": True}
            )
            catalog = PermissionCatalog(
                SQLiteDatabase(Path(directory) / "access.db"),
                resource_permissions=resources.permission_nodes(),
            )
            values = {entry.value for entry in catalog.list()}

        self.assertNotIn("team.alpha.permission_assignments.grant", values)
        self.assertNotIn("team.alpha.permission_assignments.read", values)
        self.assertNotIn("team.alpha.keys.grant", values)
        self.assertNotIn("team.alpha.keys.read", values)
        self.assertIn("team.alpha.keys.distribute", values)
        self.assertNotIn("team:<team_id>.keys.grant", values)


if __name__ == "__main__":
    unittest.main()
