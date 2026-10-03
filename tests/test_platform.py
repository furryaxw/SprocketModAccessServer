from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.modules.permission_assignments.evaluator import AuthorizationService
from sprocket_access_server.modules.system.platform import PlatformStore
from sprocket_access_server.modules.system.provisioning import SystemProvisioning


class PlatformStoreTests(unittest.TestCase):
    def test_simple_mode_creates_default_team_and_membership(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            database.claim_first_owner("1", now=10)
            store = PlatformStore(database, "simple")
            provisioning = SystemProvisioning(database, "simple")
            user = provisioning.ensure_user("1", "owner", now=11)
            self.assertEqual(user["github_user_id"], "1")
            teams = AuthorizationService(database).memberships("1")
            self.assertEqual(teams[0]["team_id"], "default")
            self.assertEqual(teams[0]["membership_status"], "permission_derived")

    def test_complex_mode_registers_user_without_team(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            store = PlatformStore(database, "complex")
            provisioning = SystemProvisioning(database, "complex")
            user = provisioning.ensure_user("2", "developer", now=11)
            self.assertEqual(user["github_user_id"], "2")
            self.assertEqual(AuthorizationService(database).memberships("2"), [])

    def test_application_uses_current_time_when_now_is_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            database.initialize()
            store = PlatformStore(database, "complex")
            SystemProvisioning(database, "complex").ensure_user("2", "developer")
            application = store.create_application("2", "Build", "", now=3)
        self.assertGreater(application["created_at"], 0)

    def test_seeded_reserved_rows_use_current_time(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            database.initialize()
            with database.transaction() as connection:
                system_user = connection.execute(
                    "SELECT created_at, updated_at FROM users WHERE github_user_id='system'"
                ).fetchone()
                teams = connection.execute(
                    "SELECT created_at, updated_at FROM teams WHERE team_id IN ('system', 'template') ORDER BY team_id"
                ).fetchall()
        self.assertGreater(system_user["created_at"], 0)
        self.assertGreater(system_user["updated_at"], 0)
        self.assertTrue(all(row["created_at"] > 0 and row["updated_at"] > 0 for row in teams))

    def test_application_approval_and_invitation_are_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            database.claim_first_owner("1", now=1)
            store = PlatformStore(database, "complex")
            provisioning = SystemProvisioning(database, "complex")
            provisioning.ensure_user("1", "owner", now=2)
            provisioning.ensure_user("2", "applicant", now=2)
            provisioning.ensure_user("3", "tester", now=2)
            application = store.create_application("2", "Build Team", "", now=3)
            approved = provisioning.approve_application(application["application_id"], "1", now=4)
            self.assertEqual(approved["status"], "approved")
            self.assertEqual(approved["team_id"], "build-team")
            with database.transaction() as connection:
                templates = connection.execute(
                    "SELECT name FROM permission_templates WHERE team_id=? ORDER BY name",
                    (approved["team_id"],),
                ).fetchall()
            self.assertEqual(
                [row["name"] for row in templates],
                ["Admin", "Developer", "Owner", "Tester"],
            )
            self.assertEqual(store.user("2")["github_user_id"], "2")
            invitation = store.invite(approved["team_id"], "3", "Tester", "2", now=5)
            accepted = provisioning.accept_invitation(invitation["token"], "3", now=6)
            self.assertEqual(accepted["permission_template_name"], "Tester")
            self.assertEqual(store.user("3")["github_user_id"], "3")

    def test_member_permission_template_change_and_removal_protect_owner_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            store = PlatformStore(database, "complex")
            provisioning = SystemProvisioning(database, "complex")
            provisioning.ensure_user("1", "owner", now=1)
            provisioning.ensure_user("2", "dev", now=1)
            database.claim_first_owner("1", now=1)
            provisioning.ensure_user("1", "owner", now=2)
            application = store.create_application("2", "Build", "", now=3)
            team_id = provisioning.approve_application(application["application_id"], "1", now=4)["team_id"]
            # team id 由名称派生。
            self.assertEqual(team_id, "build")
            with self.assertRaisesRegex(ValueError, "owner assignment"):
                provisioning.set_member_permission_template(team_id, "2", "Tester", now=5)
            provisioning.ensure_user("3", "tester", now=1)
            invitation = store.invite(team_id, "3", "Tester", "2", now=5)
            provisioning.accept_invitation(invitation["token"], "3", now=6)
            removed = provisioning.remove_member(team_id, "3", now=7)
        self.assertEqual(removed["status"], "removed")


if __name__ == "__main__":
    unittest.main()
