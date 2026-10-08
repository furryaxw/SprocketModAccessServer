from __future__ import annotations

import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from sprocket_access_server.infrastructure.database import CURRENT_SCHEMA_VERSION, SQLiteDatabase

# 升级前的 Developer 节点集合（含 Template Team 前缀）：迁移只认"一个不多一个不少"的这个集合。
LEGACY_DEVELOPER = (
    "team.template.overview.read",
    "team.template.packages.read",
    "team.template.packages.create",
    "team.template.packages.manage",
    "team.template.packages.download",
    "team.template.package_uploads.create",
    "team.template.package_uploads.confirm",
    "team.template.keys.read",
)


class DatabaseMaintenanceTests(unittest.TestCase):
    def test_initialize_repairs_zero_required_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            database.initialize()
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(github_user_id, created_at, updated_at) VALUES (?, ?, ?)",
                    ("zero", 0, 0),
                )
            database.initialize()
            with database.transaction() as connection:
                row = connection.execute(
                    "SELECT created_at, updated_at FROM users WHERE github_user_id=?",
                    ("zero",),
                ).fetchone()
        self.assertGreater(row["created_at"], 0)
        self.assertGreater(row["updated_at"], 0)

    def test_initialize_records_schema_version_and_backup_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = SQLiteDatabase(root / "access.db")
            database.initialize()
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(github_user_id, created_at, updated_at) VALUES (?, ?, ?)",
                    ("42", 1, 1),
                )
            self.assertEqual(database.schema_version, CURRENT_SCHEMA_VERSION)
            with database.transaction() as connection:
                versions = [row["version"] for row in
                            connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
                indexes = {row["name"] for row in connection.execute("PRAGMA index_list(activation_keys)")}
            self.assertEqual(versions, list(range(1, CURRENT_SCHEMA_VERSION + 1)))
            self.assertIn("idx_activation_keys_batch_id", indexes)
            backup = database.backup_to(root / "backups" / "access.db")
            self.assertTrue(SQLiteDatabase.verify_backup(backup))

            restored = root / "restored.db"
            SQLiteDatabase.restore_from(backup, restored)
            connection = sqlite3.connect(restored)
            try:
                self.assertEqual(connection.execute("SELECT github_user_id FROM users").fetchone()[0], "42")
            finally:
                connection.close()

    def test_corrupt_backup_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt.db"
            path.write_bytes(b"not a sqlite database")
            self.assertFalse(SQLiteDatabase.verify_backup(path))
            with self.assertRaises(ValueError):
                SQLiteDatabase.restore_from(path, Path(directory) / "restored.db")

    def test_initialization_loads_separate_system_and_team_template_seeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            database.initialize()
            with database.transaction() as connection:
                rows = connection.execute(
                    "SELECT template_id,name,team_id FROM permission_templates ORDER BY template_id"
                ).fetchall()
                assignments = connection.execute(
                    "SELECT template_id,node FROM template_assignments ORDER BY template_id,node"
                ).fetchall()

        self.assertEqual(
            [(row["template_id"], row["team_id"]) for row in rows],
            [
                ("template.owner", "system"),
                ("template.superadmin", "system"),
                ("template.team.admin", "template"),
                ("template.team.developer", "template"),
                ("template.team.owner", "template"),
                ("template.team.tester", "template"),
                ("template.user", "system"),
            ],
        )
        self.assertIn(("template.team.tester", "team.template.packages.read"),
                      [(row["template_id"], row["node"]) for row in assignments])
        values = {(row["template_id"], row["node"]) for row in assignments}
        self.assertIn(("template.user", "system.authentication.me.read"), values)
        self.assertIn(("template.user", "system.authentication.me.authorization.read"), values)
        self.assertIn(("template.user", "system.authentication.me.teams.read"), values)
        self.assertIn(("template.superadmin", "team.system.*"), values)
        self.assertIn(("template.superadmin", "system.*"), values)
        self.assertIn(("template.team.admin", "team.template.packages.download"), values)
        self.assertIn(("template.team.admin", "team.template.keys.distribute"), values)
        self.assertIn(("template.team.admin", "team.template.permission_assignments.manage"), values)

    def test_version_six_database_requires_clean_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            with patch("sprocket_access_server.infrastructure.database.sqlite.CURRENT_SCHEMA_VERSION", 6):
                database.initialize()
            with database.transaction() as connection:
                versions = [row["version"] for row in
                            connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
            self.assertEqual(versions, [1, 2, 3, 4, 5, 6])
            with self.assertRaisesRegex(RuntimeError, "clean reset"):
                database.initialize()

    def test_developer_template_gains_package_confirm_once(self) -> None:
        """Developer 拿到 `packages.confirm`（版本状态/删除要用它）。

        种子只影响新建 Team，已有 Team 的模板克隆靠一次性数据迁移补；迁移必须只跑一次，
        否则管理员事后撤掉这个节点会被重启复原。
        """
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            database.initialize()
            with database.transaction() as connection:
                seeded = sorted(
                    str(row["node"]) for row in connection.execute(
                        "SELECT node FROM template_assignments WHERE template_id='template.team.developer'"
                    ).fetchall()
                )
                self._stage_legacy_team(connection, "legacy", extra=())
                self._stage_legacy_team(connection, "custom", extra=("team.custom.keys.manage",))
                # 已有库里的固定 Template Team 那一行也是旧的节点集合：种子按 `id:index` 重放即可补上。
                connection.execute(
                    "DELETE FROM template_assignments WHERE template_id='template.team.developer' "
                    "AND node='team.template.packages.confirm'"
                )
                connection.execute("DELETE FROM data_migrations WHERE name='developer-package-confirm'")
            database.initialize()
            with database.transaction() as connection:
                restored = sorted(
                    str(row["node"]) for row in connection.execute(
                        "SELECT node FROM template_assignments WHERE template_id='template.team.developer'"
                    ).fetchall()
                )
                upgraded = sorted(
                    str(row["node"]) for row in connection.execute(
                        "SELECT node FROM template_assignments WHERE template_id='legacy:template.team.developer'"
                    ).fetchall()
                )
                customized = sorted(
                    str(row["node"]) for row in connection.execute(
                        "SELECT node FROM template_assignments WHERE template_id='custom:template.team.developer'"
                    ).fetchall()
                )
                catalog = {
                    str(row["node"]) for row in connection.execute(
                        "SELECT node FROM permission_nodes WHERE node LIKE 'team.legacy.packages%'"
                    ).fetchall()
                }
                # 迁移只跑一次：撤掉之后重启不再加回来。
                connection.execute(
                    "DELETE FROM template_assignments WHERE template_id='legacy:template.team.developer' "
                    "AND node='team.legacy.packages.confirm'"
                )
            database.initialize()
            with database.transaction() as connection:
                reverted = sorted(
                    str(row["node"]) for row in connection.execute(
                        "SELECT node FROM template_assignments WHERE template_id='legacy:template.team.developer'"
                    ).fetchall()
                )

        self.assertIn("team.template.packages.confirm", seeded)
        self.assertIn("team.template.packages.confirm", restored)
        self.assertEqual(upgraded, sorted([*self._scoped(LEGACY_DEVELOPER, "legacy"),
                                           "team.legacy.packages.confirm"]))
        # 定制过的模板不碰。
        self.assertEqual(customized, sorted([*self._scoped(LEGACY_DEVELOPER, "custom"),
                                             "team.custom.keys.manage"]))
        self.assertIn("team.legacy.packages.confirm", catalog)
        self.assertNotIn("team.legacy.packages.confirm", reverted)

    def test_legacy_expanded_developer_grant_gains_package_confirm(self) -> None:
        """老 Team 成员手里是展开节点（不是模板实例节点），迁移同样按旧集合识别并补齐。"""
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            database.initialize()
            with database.transaction() as connection:
                for user_id in ("dev", "custom-dev"):
                    connection.execute(
                        "INSERT INTO users(github_user_id,login_snapshot,created_at,updated_at) "
                        "VALUES(?,'dev',1,1)", (user_id,),
                    )
                self._stage_legacy_grant(connection, "legacy-dev", "dev", "legacy", extra=())
                self._stage_legacy_grant(connection, "custom-dev", "custom-dev", "custom",
                                         extra=("team.custom.keys.manage",))
                connection.execute("DELETE FROM data_migrations WHERE name='developer-package-confirm'")
            database.initialize()
            with database.transaction() as connection:
                upgraded = sorted(
                    str(row["node"]) for row in connection.execute(
                        "SELECT node FROM grant_assignments WHERE grant_id='legacy-dev'"
                    ).fetchall()
                )
                customized = sorted(
                    str(row["node"]) for row in connection.execute(
                        "SELECT node FROM grant_assignments WHERE grant_id='custom-dev'"
                    ).fetchall()
                )

        self.assertEqual(upgraded, sorted([*self._scoped(LEGACY_DEVELOPER, "legacy"),
                                           "team.legacy.packages.confirm"]))
        self.assertEqual(customized, sorted([*self._scoped(LEGACY_DEVELOPER, "custom"),
                                             "team.custom.keys.manage"]))

    @staticmethod
    def _scoped(nodes, team_id: str) -> list[str]:
        return sorted(str(node).replace("team.template.", f"team.{team_id}.", 1) for node in nodes)

    def _stage_legacy_team(self, connection, team_id: str, *, extra) -> None:
        template_id = f"{team_id}:template.team.developer"
        connection.execute(
            """INSERT INTO teams(team_id,name,description,status,owner_user_id,created_by,created_at,updated_at)
               VALUES(?,?,'','active','system','system',1,1)""",
            (team_id, team_id),
        )
        connection.execute(
            """INSERT INTO permission_templates(template_id,name,template_kind,team_id,created_by,created_at)
               VALUES(?,'Developer','permission_template',?,'system',1)""",
            (template_id, team_id),
        )
        for index, node in enumerate([*self._scoped(LEGACY_DEVELOPER, team_id), *extra]):
            connection.execute(
                """INSERT INTO template_assignments
                       (assignment_id,template_id,node,effect,priority,grant_effect)
                   VALUES(?,?,?,'allow',0,'allow')""",
                (f"{template_id}:{index}", template_id, node),
            )

    def _stage_legacy_grant(self, connection, grant_id: str, user_id: str, team_id: str, *, extra) -> None:
        connection.execute(
            """INSERT INTO grants
                   (grant_id,github_user_id,source_type,source_id,team_id,status,created_by,created_at,updated_at)
               VALUES(?,?,'permission_template','Developer',?,'active','system',1,1)""",
            (grant_id, user_id, team_id),
        )
        for index, node in enumerate([*self._scoped(LEGACY_DEVELOPER, team_id), *extra]):
            connection.execute(
                """INSERT INTO grant_assignments
                       (assignment_id,grant_id,node,effect,priority,grant_effect,source_type,source_id)
                   VALUES(?,?,?,'allow',0,'allow','permission_template','Developer')""",
                (f"{grant_id}:{index}", grant_id, node),
            )


if __name__ == "__main__":
    unittest.main()
