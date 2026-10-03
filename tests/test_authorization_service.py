from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.modules.permission_assignments.evaluator import AuthorizationService


def insert_grant(connection, *, grant_id: str, user_id: str, team_id: str | None, node: str,
                 grant_effect: str = "allow") -> None:
    connection.execute(
        """INSERT INTO grants
           (grant_id,github_user_id,source_type,source_id,team_id,status,created_by,created_at,updated_at)
           VALUES(?,?, 'direct', ?, ?, 'active', 'test', 1, 1)""",
        (grant_id, user_id, grant_id, team_id),
    )
    connection.execute(
        """INSERT INTO grant_assignments
           (assignment_id,grant_id,node,effect,priority,grant_effect,source_type,source_id)
           VALUES(?,?,?,?,10,?,'direct',?)""",
        (f"{grant_id}:0", grant_id, node, "allow", grant_effect, grant_id),
    )


class AuthorizationServiceTests(unittest.TestCase):
    def test_member_template_grant_stores_the_instance_node(self) -> None:
        """成员授权只记模板实例节点：改模板立刻生效，不需要重新分配。"""
        from sprocket_access_server.modules.system.provisioning import set_member_permission_template

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = SQLiteDatabase(Path(tmp.name) / "access.db")
        db.initialize()
        with db.transaction() as connection:
            connection.execute("INSERT INTO users(github_user_id,created_at,updated_at) VALUES('owner',1,1)")
            connection.execute("INSERT INTO users(github_user_id,created_at,updated_at) VALUES('member',1,1)")
            connection.execute(
                "INSERT INTO teams(team_id,name,owner_user_id,created_by,created_at,updated_at)"
                " VALUES('a','A','owner','owner',1,1)"
            )
            connection.execute(
                "INSERT INTO permission_templates(template_id,name,template_kind,team_id,created_by,created_at)"
                " VALUES('a:template.team.tester','Tester','permission_template','a','system',1)"
            )
            connection.execute(
                "INSERT INTO template_assignments(assignment_id,template_id,node,effect,priority,grant_effect)"
                " VALUES('a:template.team.tester:0','a:template.team.tester','team.a.packages.read','allow',0,'allow')"
            )

        set_member_permission_template(db, "a", "member", "Tester", now=10)

        with db.transaction() as connection:
            rows = connection.execute(
                "SELECT node FROM grant_assignments WHERE grant_id='team-template:a:member'"
            ).fetchall()
        self.assertEqual([str(row["node"]) for row in rows], ["team.a.templates.a-template-team-tester"])

        service = AuthorizationService(db)

        def effective(now: int) -> set[str]:
            return {item.node for item in service.context("member", team_id="a", now=now).permission_assignments}

        self.assertEqual(effective(11), {"team.a.packages.read"})

        with db.transaction() as connection:
            connection.execute(
                "INSERT INTO template_assignments(assignment_id,template_id,node,effect,priority,grant_effect)"
                " VALUES('a:template.team.tester:1','a:template.team.tester','team.a.packages.download','allow',0,'allow')"
            )
        self.assertEqual(effective(12), {"team.a.packages.read", "team.a.packages.download"})

    def setup_service(self):
        tmp = TemporaryDirectory()
        db = SQLiteDatabase(Path(tmp.name) / "access.db")
        db.initialize()
        with db.transaction() as connection:
            connection.execute(
                "INSERT INTO users(github_user_id,created_at,updated_at) VALUES('u',1,1)"
            )
            connection.execute(
                "INSERT INTO teams(team_id,name,owner_user_id,created_by,created_at,updated_at) VALUES('a','A','u','u',1,1)"
            )
            connection.execute(
                "INSERT INTO teams(team_id,name,owner_user_id,created_by,created_at,updated_at) VALUES('b','B','u','u',1,1)"
            )
            insert_grant(connection, grant_id="ga", user_id="u", team_id="a", node="team.a.keys.manage")
            insert_grant(connection, grant_id="gb", user_id="u", team_id="b", node="team.b.packages.read")
        return tmp, AuthorizationService(db), db

    def test_team_context_uses_assignment_snapshot(self):
        tmp, service, _ = self.setup_service()
        try:
            a = service.context("u", team_id="a")
            b = service.context("u", team_id="b")
        finally:
            tmp.cleanup()
        self.assertIn("team.a.keys.manage", a.effective_permissions)
        self.assertNotIn("team.a.keys.manage", b.effective_permissions)
        self.assertIn("team.b.packages.read", b.effective_permissions)

    def test_system_permission_is_independent_of_team_context(self):
        tmp, service, db = self.setup_service()
        try:
            with db.transaction() as connection:
                insert_grant(connection, grant_id="system", user_id="u", team_id=None, node="system.teams.read")
            context = service.require_system("u", "system.teams.read")
            self.assertIsNone(context.team_id)
        finally:
            tmp.cleanup()

    def test_team_assignment_cannot_satisfy_system_permission(self):
        tmp, service, _ = self.setup_service()
        try:
            with self.assertRaisesRegex(Exception, "system permission"):
                service.require_system("u", "system.teams.manage")
        finally:
            tmp.cleanup()

    def test_missing_team_context_fails_closed(self):
        tmp, service, _ = self.setup_service()
        try:
            with self.assertRaisesRegex(Exception, "Team context"):
                service.require_team("u", None, "packages.read")
        finally:
            tmp.cleanup()

    def test_archived_team_is_not_a_workspace_membership(self):
        tmp, service, db = self.setup_service()
        try:
            with db.transaction() as connection:
                connection.execute("UPDATE teams SET status='archived' WHERE team_id='b'")
            memberships = service.memberships("u")
        finally:
            tmp.cleanup()
        self.assertEqual([item["team_id"] for item in memberships], ["a"])

    def test_wildcard_owner_assignment_can_inspect_team(self):
        tmp, service, db = self.setup_service()
        try:
            with db.transaction() as connection:
                insert_grant(connection, grant_id="owner", user_id="u", team_id=None, node="*")
            context = service.context("u", team_id="b")
            self.assertEqual(context.team_id, "b")
        finally:
            tmp.cleanup()

    def test_context_includes_content_grant_assignments(self):
        tmp, service, db = self.setup_service()
        try:
            with db.transaction() as connection:
                insert_grant(
                    connection,
                    grant_id="package",
                    user_id="u",
                    team_id="a",
                    node="team.a.packages.mod.download",
                )
            context = service.context("u", team_id="a", now=2)
        finally:
            tmp.cleanup()
        self.assertIn("team.a.packages.mod.download", context.effective_permissions)
        self.assertTrue(any(item.assignment_id == "package:0" for item in context.permission_assignments))

    def test_require_grantable_rejects_permission_outside_effective_scope(self):
        tmp, service, _ = self.setup_service()
        try:
            with self.assertRaisesRegex(Exception, "cannot be granted"):
                service.require_grantable("u", ["team.a.packages.mod.download"], team_id="a", now=2)
        finally:
            tmp.cleanup()

    def test_template_grant_uses_live_template_assignments(self):
        tmp, service, db = self.setup_service()
        try:
            with db.transaction() as connection:
                connection.execute(
                    "INSERT INTO permission_templates(template_id,name,template_kind,team_id,status,created_by,created_at) VALUES('t','Live','permission_template','a','active','u',1)"
                )
                connection.execute(
                    "INSERT INTO template_assignments(assignment_id,template_id,node,effect,priority,grant_effect) VALUES('ta','t','team.a.packages.old.download','allow',1,'allow')"
                )
                connection.execute(
                    "INSERT INTO grants(grant_id,github_user_id,source_type,source_id,template_id,team_id,status,created_by,created_at,updated_at) VALUES('g','u','permission_template','t','t','a','active','u',1,1)"
                )
            before = service.context("u", team_id="a", now=2)
            with db.transaction() as connection:
                connection.execute("UPDATE template_assignments SET node='team.a.packages.new.download' WHERE assignment_id='ta'")
            after = service.context("u", team_id="a", now=3)
        finally:
            tmp.cleanup()
        self.assertIn("team.a.packages.old.download", before.effective_permissions)
        self.assertNotIn("team.a.packages.old.download", after.effective_permissions)
        self.assertIn("team.a.packages.new.download", after.effective_permissions)

    def test_require_grantable_rejects_grant_denied_assignment(self):
        tmp, service, db = self.setup_service()
        try:
            with db.transaction() as connection:
                insert_grant(
                    connection,
                    grant_id="deny-grant",
                    user_id="u",
                    team_id="a",
                    node="team.a.packages.mod.download",
                    grant_effect="deny",
                )
            with self.assertRaisesRegex(Exception, "cannot be granted"):
                service.require_grantable("u", ["team.a.packages.mod.download"], team_id="a", now=2)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
