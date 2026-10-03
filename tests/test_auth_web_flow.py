import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.modules.system.authentication import AuthenticationService
from sprocket_access_server.core.ports import GitHubIdentity
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.security.sessions import SQLiteSessionStore
from sprocket_access_server.modules.system.provisioning import SystemProvisioning


class FakeGitHub:
    def exchange_web_code(self, client_id, client_secret, code, redirect_uri):
        assert client_id == "client"
        assert client_secret == "secret"
        assert code == "code"
        assert redirect_uri.endswith("/callback")
        return {"access_token": "token"}

    def verify_access_token(self, token):
        assert token == "token"
        return GitHubIdentity("123", "owner")

    def start_device_flow(self, client_id):
        assert client_id == "client"
        return {"device_code": "device-code", "user_code": "ABCD-EFGH", "verification_uri": "https://github.com/login/device", "interval": 5, "expires_in": 900}

    def poll_device_flow(self, client_id, device_code):
        assert client_id == "client"
        assert device_code == "device-code"
        return {"access_token": "token"}


class WebFlowTests(unittest.TestCase):
    def test_pending_flow_tables_stay_bounded(self):
        """两个表由公开动作写入：过期条目要被清掉，容量要有上限。"""
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            auth = AuthenticationService(
                FakeGitHub(), SQLiteSessionStore(database, b"pepper"),
                session_ttl=100, ownership=database,
                provisioning=SystemProvisioning(database, "complex"),
                github_device_client_id="client", github_client_secret="secret",
                github_callback_url="http://127.0.0.1:8787/v1/auth/github/callback",
            )
            limit = auth._MAX_PENDING_FLOWS
            for index in range(limit * 2):
                auth.start_github_web_flow(now=100 + index)
            self.assertEqual(len(auth._web_states), limit)
            # 全部过期后再开一次，过期条目先被清掉。
            auth.start_github_web_flow(now=10_000)
            self.assertEqual(len(auth._web_states), 1)

            for index in range(limit * 2):
                auth.start_github_device_flow(now=100 + index)
            self.assertEqual(len(auth._device_codes), limit)

    def test_state_is_single_use_and_code_exchange_creates_session(self):
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            auth = AuthenticationService(
                FakeGitHub(), SQLiteSessionStore(database, b"pepper"),
                session_ttl=100, ownership=database,
                provisioning=SystemProvisioning(database, "complex"),
                github_device_client_id="client", github_client_secret="secret",
                github_callback_url="http://127.0.0.1:8787/v1/auth/github/callback",
            )
            flow = auth.start_github_web_flow(now=100)
            self.assertIn("client_id=client", flow["authorization_url"])
            self.assertEqual(flow["callback_url"], "http://127.0.0.1:8787/v1/auth/github/callback")
            result = auth.complete_github_web_flow("code", flow["state"], now=101)
            self.assertEqual(auth.authenticate_session(result.token, now=150), "123")
            with self.assertRaises(Exception):
                auth.complete_github_web_flow("code", flow["state"], now=102)

    def test_web_flow_requires_client_secret_before_starting(self):
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            auth = AuthenticationService(
                FakeGitHub(), SQLiteSessionStore(database, b"pepper"),
                github_device_client_id="client",
                github_callback_url="http://127.0.0.1:8787/v1/auth/github/callback",
            )

            with self.assertRaises(Exception) as raised:
                auth.start_github_web_flow(now=100)

        self.assertEqual(getattr(raised.exception, "code", None), "github_oauth_unavailable")
        self.assertEqual(auth.github_auth_methods()["github"]["web"], False)
        self.assertEqual(auth.github_auth_methods()["github"]["device"], True)

    def test_device_flow_exchanges_completed_authorization_for_session(self):
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            auth = AuthenticationService(
                FakeGitHub(), SQLiteSessionStore(database, b"pepper"),
                session_ttl=100, ownership=database,
                provisioning=SystemProvisioning(database, "complex"),
                github_device_client_id="client",
            )

            flow = auth.start_github_device_flow(now=100)
            result = auth.poll_github_device_flow(str(flow["flow_id"]), now=101)
            user_id = auth.authenticate_session(result.token, now=150)

        self.assertEqual(user_id, "123")


if __name__ == "__main__":
    unittest.main()
