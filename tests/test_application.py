from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.modules.system.authentication import AuthenticationService, RegistrationPolicy
from sprocket_access_server.core.ports import GitHubIdentity, GitHubIdentityError
from sprocket_access_server.domain.errors import ApiError
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.security.sessions import SQLiteSessionStore
from sprocket_access_server.modules.system.platform import PlatformStore
from sprocket_access_server.modules.system.provisioning import SystemProvisioning


class FakeGitHub:
    def __init__(self, identity: GitHubIdentity | None = None, error: Exception | None = None):
        self.identity = identity
        self.error = error

    def verify_access_token(self, access_token: str) -> GitHubIdentity:
        if self.error:
            raise self.error
        assert self.identity is not None
        return self.identity


class AuthenticationServiceTests(unittest.TestCase):
    def service(self, directory: str, github: FakeGitHub) -> AuthenticationService:
        database = SQLiteDatabase(Path(directory) / "access.db")
        sessions = SQLiteSessionStore(database, b"pepper")
        return AuthenticationService(
            github,
            sessions,
            session_ttl=100,
            provisioning=SystemProvisioning(database, "complex"),
        )

    def test_exchange_verifies_github_and_creates_server_session(self) -> None:
        with TemporaryDirectory() as directory:
            service = self.service(directory, FakeGitHub(GitHubIdentity("123", "renamed")))
            result = service.exchange_github_token("github-token", now=100)
            self.assertEqual(result.github_user_id, "123")
            self.assertEqual(service.authenticate_session(result.token, now=199), "123")

    def test_github_failure_is_stable_and_does_not_leak_provider_error(self) -> None:
        with TemporaryDirectory() as directory:
            service = self.service(directory, FakeGitHub(error=RuntimeError("secret provider detail")))
            with self.assertRaises(ApiError) as raised:
                service.exchange_github_token("github-token", now=100)
        self.assertEqual(raised.exception.code, "github_identity_failed")
        self.assertNotIn("secret provider detail", str(raised.exception))

    def test_invalid_session_has_stable_code(self) -> None:
        with TemporaryDirectory() as directory:
            service = self.service(directory, FakeGitHub(GitHubIdentity("123", "user")))
            with self.assertRaises(ApiError) as raised:
                service.authenticate_session("missing", now=100)
        self.assertEqual(raised.exception.code, "invalid_session")

    def test_github_token_rejection_has_actionable_code(self) -> None:
        for status, code in ((401, "github_token_rejected"), (403, "github_token_forbidden")):
            with self.subTest(status=status):
                with TemporaryDirectory() as directory:
                    service = self.service(directory, FakeGitHub(error=GitHubIdentityError("rejected", status=status)))
                    with self.assertRaises(ApiError) as raised:
                        service.exchange_github_token("github-token", now=100)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.status, status)

    def test_github_outage_has_retryable_code(self) -> None:
        with TemporaryDirectory() as directory:
            service = self.service(directory, FakeGitHub(error=GitHubIdentityError("offline", unavailable=True)))
            with self.assertRaises(ApiError) as raised:
                service.exchange_github_token("github-token", now=100)
        self.assertEqual(raised.exception.code, "github_identity_unavailable")
        self.assertEqual(raised.exception.status, 503)

    def test_first_login_receives_owner_and_user_templates(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            sessions = SQLiteSessionStore(database, b"pepper")
            platform = PlatformStore(database, "complex")
            provisioning = SystemProvisioning(database, "complex")
            service = AuthenticationService(
                FakeGitHub(GitHubIdentity("123", "owner")),
                sessions,
                ownership=database,
                provisioning=provisioning,
            )

            service.exchange_github_token("github-token", now=100)

            with database.transaction() as connection:
                templates = [
                    row["template_id"]
                    for row in connection.execute(
                        "SELECT template_id FROM grants WHERE github_user_id=? ORDER BY template_id",
                        ("123",),
                    ).fetchall()
                ]
        self.assertEqual(templates, ["template.owner", "template.user"])

    def test_later_login_receives_user_template_only(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            sessions = SQLiteSessionStore(database, b"pepper")
            platform = PlatformStore(database, "complex")
            provisioning = SystemProvisioning(database, "complex")
            first = AuthenticationService(
                FakeGitHub(GitHubIdentity("123", "owner")),
                sessions,
                ownership=database,
                provisioning=provisioning,
            )
            second = AuthenticationService(
                FakeGitHub(GitHubIdentity("456", "user")),
                sessions,
                ownership=database,
                provisioning=provisioning,
            )

            first.exchange_github_token("first-token", now=100)
            second.exchange_github_token("second-token", now=101)

            with database.transaction() as connection:
                templates = [
                    row["template_id"]
                    for row in connection.execute(
                        "SELECT template_id FROM grants WHERE github_user_id=? ORDER BY template_id",
                        ("456",),
                    ).fetchall()
                ]
        self.assertEqual(templates, ["template.user"])

    def test_disabled_auto_registration_rejects_new_users_before_session_creation(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            sessions = SQLiteSessionStore(database, b"pepper")
            platform = PlatformStore(database, "complex")
            provisioning = SystemProvisioning(database, "complex")
            service = AuthenticationService(
                FakeGitHub(GitHubIdentity("123", "new-user")),
                sessions,
                ownership=database,
                provisioning=provisioning,
                registration_policy=RegistrationPolicy(auto_register_users=False),
            )

            with self.assertRaises(ApiError) as raised:
                service.exchange_github_token("github-token", now=100)

            with database.transaction() as connection:
                sessions_count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        self.assertEqual(raised.exception.code, "registration_disabled")
        self.assertEqual(sessions_count, 0)

    def test_missing_provisioning_rejects_session_creation(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            sessions = SQLiteSessionStore(database, b"pepper")
            service = AuthenticationService(
                FakeGitHub(GitHubIdentity("123", "user")),
                sessions,
                ownership=database,
            )

            with self.assertRaises(ApiError) as raised:
                service.exchange_github_token("github-token", now=100)

            with database.transaction() as connection:
                sessions_count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        self.assertEqual(raised.exception.code, "registration_unavailable")
        self.assertEqual(sessions_count, 0)


if __name__ == "__main__":
    unittest.main()
