from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.modules.permission_templates.store import SQLiteGrantTemplateStore


class GrantTemplateTests(unittest.TestCase):
    def test_create_list_disable_and_reject_duplicate_names(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteGrantTemplateStore(SQLiteDatabase(Path(directory) / "access.db"))
            template = store.create(name="Tester", permissions=frozenset({"team.default.packages.mod-a.download"}), expires_in=3600,
                                    created_by="123", now=10)
            self.assertEqual(store.get(template.template_id).permissions, frozenset({"team.default.packages.mod-a.download"}))
            with self.assertRaises(ValueError):
                store.create(name="Tester", permissions=frozenset({"team.default.packages.mod-b.download"}), expires_in=None,
                             created_by="123", now=11)
            store.set_status(template.template_id, "disabled")
            self.assertIsNone(store.get(template.template_id))
            self.assertNotIn("Tester", [item.name for item in store.list()])
            disabled = [item for item in store.list(active_only=False) if item.name == "Tester"]
            self.assertEqual(len(disabled), 1)
            self.assertEqual(disabled[0].status, "disabled")

    def test_template_can_be_updated_and_disabled(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteGrantTemplateStore(SQLiteDatabase(Path(directory) / "access.db"))
            template = store.create(name="Tester", permissions=frozenset({"team.default.packages.mod-a.download"}), expires_in=3600,
                                    created_by="admin", team_id="team-a", now=1)
            updated = store.update(template.template_id, name="QA", permissions=frozenset({"team.default.packages.mod-a.read"}),
                                   expires_in=None, team_id="team-a")
            store.set_status(template.template_id, "disabled", team_id="team-a")
            disabled = store.get(template.template_id, active_only=False, team_id="team-a")
        self.assertEqual(updated.name, "QA")
        self.assertEqual(updated.permissions, frozenset({"team.default.packages.mod-a.read"}))
        self.assertEqual(disabled.status, "disabled")

    def test_template_update_is_team_scoped(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteGrantTemplateStore(SQLiteDatabase(Path(directory) / "access.db"))
            template = store.create(name="Tester", permissions=frozenset({"team.default.packages.mod-a.download"}), expires_in=None,
                                    created_by="admin", team_id="team-a", now=1)
            with self.assertRaisesRegex(ValueError, "not found"):
                store.update(template.template_id, name="Leak", permissions=frozenset({"team.default.packages.mod-b.download"}),
                             expires_in=None, team_id="team-b")


if __name__ == "__main__":
    unittest.main()
