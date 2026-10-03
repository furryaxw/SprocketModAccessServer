from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.domain.errors import ApiError
from sprocket_access_server.modules.permission_assignments.evaluator import AuthorizationService
from sprocket_access_server.infrastructure.database import SQLiteDatabase


class AuthorizationBoundaryTests(unittest.TestCase):
    def test_owner_receives_seeded_system_permissions(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            database.initialize()
            database.claim_first_owner("123", now=1)
            context = AuthorizationService(database).require_team("123", "system", "users.read")

        self.assertIn("*", context.effective_permissions)

    def test_user_without_system_permission_is_forbidden(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            database.initialize()
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(github_user_id,created_at,updated_at) VALUES('123',1,1)"
                )
            with self.assertRaises(ApiError) as raised:
                AuthorizationService(database).require_team("123", "system", "users.read")

        self.assertEqual(raised.exception.code, "team_permission_denied")


if __name__ == "__main__":
    unittest.main()
