from __future__ import annotations

import os
import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sprocket_access_server.core.runtime import build_app
from sprocket_access_server.infrastructure.database import SQLiteDatabase


class RuntimeTests(unittest.TestCase):
    def test_runtime_requires_session_pepper(self) -> None:
        old = os.environ.pop("SMAS_SESSION_PEPPER", None)
        try:
            with TemporaryDirectory() as directory:
                with self.assertRaisesRegex(RuntimeError, "SMAS_SESSION_PEPPER"):
                    build_app(base_dir=Path(directory))
        finally:
            if old is not None:
                os.environ["SMAS_SESSION_PEPPER"] = old

    def test_clean_sqlite_runtime_serves_health(self) -> None:
        private_key = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        environment = {
            "SMAS_DATABASE_URL": "sqlite:///access.db",
            "SMAS_OBJECT_STORAGE_URL": "file:./packages",
            "SMAS_PLATFORM_MODE": "simple",
            "SMAS_SESSION_PEPPER": "runtime-session-secret",
            "SMAS_KEY_PEPPER": "runtime-key-secret",
            "SMAS_DOWNLOAD_TOKEN_SECRET": "runtime-download-secret",
            "SMAS_SIGNING_KEY_ID": "runtime-key",
            "SMAS_SIGNING_PRIVATE_KEY_FILE": "",
            "SMAS_SIGNING_PRIVATE_KEY": private_key,
            "SMAS_SMTP_HOST": "",
            "SMAS_EMAIL_FROM": "",
        }
        with TemporaryDirectory() as directory, patch.dict(os.environ, environment, clear=False):
            app = build_app(base_dir=Path(directory))
            self.assertEqual(self._request(app, "GET", "/healthz")[0], 200)

    def test_service_accounts_are_provisioned_before_first_exchange(self) -> None:
        private_key = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        environment = {
            "SMAS_DATABASE_URL": "sqlite:///access.db",
            "SMAS_OBJECT_STORAGE_URL": "file:./packages",
            "SMAS_PLATFORM_MODE": "simple",
            "SMAS_SESSION_PEPPER": "runtime-session-secret",
            "SMAS_KEY_PEPPER": "runtime-key-secret",
            "SMAS_DOWNLOAD_TOKEN_SECRET": "runtime-download-secret",
            "SMAS_SIGNING_KEY_ID": "runtime-key",
            "SMAS_SIGNING_PRIVATE_KEY_FILE": "",
            "SMAS_SIGNING_PRIVATE_KEY": private_key,
            "SMAS_SMTP_HOST": "",
            "SMAS_EMAIL_FROM": "",
            "SMAS_SERVICE_ACCOUNTS": json.dumps({"discord-bot": "runtime-service-secret-0123456789"}),
        }
        with TemporaryDirectory() as directory, patch.dict(os.environ, environment, clear=False):
            app = build_app(base_dir=Path(directory))
            # 兑换发生前该行就要存在，管理员才能先给它分配节点。
            database = SQLiteDatabase(Path(directory) / "access.db")
            with database.transaction() as connection:
                row = connection.execute(
                    "SELECT status FROM users WHERE github_user_id=?", ("discord-bot",)
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "active")

            status, payload = self._request(
                app, "POST", "/v1/auth/service/exchange", body=json.dumps({
                    "service_id": "discord-bot", "secret": "runtime-service-secret-0123456789",
                }).encode(),
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["github_user_id"], "discord-bot")
        self.assertIn("expires_at", payload)

    @staticmethod
    def _request(app, method: str, path: str, *, accept: str = "application/json", body: bytes = b""):
        sent = []
        request_messages = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive():
            return request_messages.pop(0) if request_messages else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        asyncio.run(app({
            "type": "http", "method": method, "path": path, "query_string": b"",
            "headers": [(b"accept", accept.encode("ascii"))], "client": ("test", 1),
            "server": ("test", 80), "scheme": "http", "http_version": "1.1",
        }, receive, send))
        start = next(item for item in sent if item["type"] == "http.response.start")
        body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            body = body.decode("utf-8")
        return int(start["status"]), body


if __name__ == "__main__":
    unittest.main()
