from __future__ import annotations

import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from sprocket_access_server.infrastructure.database import CURRENT_SCHEMA_VERSION, SQLiteDatabase


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


if __name__ == "__main__":
    unittest.main()
