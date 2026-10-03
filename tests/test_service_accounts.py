from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sprocket_access_server.core.contracts import ModuleContext, attach_modules, register_modules
from sprocket_access_server.core.ports import GitHubIdentity
from sprocket_access_server.domain.errors import ApiError
from sprocket_access_server.domain.protocol import ServerInfo
from sprocket_access_server.infrastructure.configuration.settings import ServerSettings
from sprocket_access_server.infrastructure.database import SchemaRegistry, SQLiteDatabase
from sprocket_access_server.infrastructure.events import InfrastructureEventBus
from sprocket_access_server.infrastructure.network import ResourceRegistry
from sprocket_access_server.infrastructure.permissions import PermissionCatalog
from sprocket_access_server.infrastructure.security.sessions import SQLiteSessionStore
from sprocket_access_server.modules.permission_assignments.evaluator import AuthorizationService
from sprocket_access_server.modules.registry import registered_modules
from sprocket_access_server.modules.system.authentication import AuthenticationService
from sprocket_access_server.modules.system.platform import PlatformStore
from sprocket_access_server.modules.system.provisioning import SystemProvisioning
from sprocket_access_server.presentation.asgi import create_app
from sprocket_access_server.presentation.authorization import authorize_resource_operation

SERVICE_ID = "discord-bot"
SERVICE_SECRET = "s3cret-service-credential-0123456789"


class FakeGitHub:
    def verify_access_token(self, _token: str) -> GitHubIdentity:
        return GitHubIdentity("123", "admin")


def _http_call(app, method: str, path: str, *, body: bytes = b"",
               headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, object]] = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(
        {"type": "http", "method": method, "path": path, "headers": raw_headers, "query_string": b"",
         "client": ("test", 1), "server": ("test", 80), "scheme": "http", "http_version": "1.1"},
        receive, send,
    ))
    start = next(item for item in sent if item["type"] == "http.response.start")
    payload = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        decoded = payload.decode("utf-8")
    return int(start["status"]), decoded


def _build(directory: str, service_accounts: dict[str, str]):
    """按 runtime 的装配顺序搭出可调用资源面的最小上下文。"""
    database = SQLiteDatabase(Path(directory) / "access.db")
    provisioning = SystemProvisioning(database, "complex")
    authentication = AuthenticationService(
        FakeGitHub(), SQLiteSessionStore(database, b"pepper"),
        session_ttl=600, ownership=database,
        provisioning=provisioning,
        service_accounts=service_accounts,
    )
    authorization_service = AuthorizationService(database)
    context = ModuleContext(
        database=SchemaRegistry(database),
        events=InfrastructureEventBus(),
        resources=ResourceRegistry(),
    )
    modules = register_modules(context, registered_modules())
    context.services.update({
        "database": database,
        "authentication": authentication,
        "authorization_service": authorization_service,
        "platform": PlatformStore(database, "complex"),
        "provisioning": provisioning,
        "permissions": PermissionCatalog(database, resource_permissions=context.resources.permission_nodes()),
    })
    attach_modules(context, modules)
    return database, authentication, authorization_service, context


class ServiceAccountSettingsTests(unittest.TestCase):
    def test_absent_variable_yields_no_service_accounts(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ServerSettings.from_environment().service_accounts, ())

    def test_valid_object_is_parsed(self):
        other = "another-service-credential-0123456789"
        raw = json.dumps({"discord-bot": SERVICE_SECRET, "audit-bot": other})
        with patch.dict(os.environ, {"SMAS_SERVICE_ACCOUNTS": raw}, clear=True):
            settings = ServerSettings.from_environment()
        self.assertEqual(settings.service_accounts, (("audit-bot", other), ("discord-bot", SERVICE_SECRET)))

    def test_short_secret_is_rejected(self):
        raw = json.dumps({"discord-bot": "too-short"})
        with patch.dict(os.environ, {"SMAS_SERVICE_ACCOUNTS": raw}, clear=True):
            with self.assertRaisesRegex(ValueError, "at least 32 characters"):
                ServerSettings.from_environment()

    def test_numeric_service_id_is_rejected(self):
        """真实身份是 GitHub 数字 id，数字保留 id 会被真人账号占用。"""
        with patch.dict(os.environ, {"SMAS_SERVICE_ACCOUNTS": '{"10000001": "s"}'}, clear=True):
            with self.assertRaises(ValueError) as raised:
                ServerSettings.from_environment()
        self.assertIn("must not be numeric", str(raised.exception))

    def test_invalid_payloads_are_rejected(self):
        for raw in ('{', '["discord-bot"]', '{"": "s"}', '{"discord-bot": ""}', '{"discord-bot": "  "}',
                    '{"discord-bot": 123}', '{"discord-bot": null}'):
            with self.subTest(raw=raw):
                with patch.dict(os.environ, {"SMAS_SERVICE_ACCOUNTS": raw}, clear=True):
                    with self.assertRaises(ValueError):
                        ServerSettings.from_environment()


class ServiceCredentialExchangeTests(unittest.TestCase):
    def test_correct_secret_issues_a_session_for_the_service_account(self):
        with TemporaryDirectory() as directory:
            database, authentication, _authorization, _context = _build(directory, {SERVICE_ID: SERVICE_SECRET})
            result = authentication.exchange_service_credential(SERVICE_ID, SERVICE_SECRET, now=100)

            self.assertEqual(authentication.authenticate_session(result.token, now=150), SERVICE_ID)
            self.assertEqual(result.expires_at, 700)
            row = SystemProvisioning(database, "complex").ensure_service_account(SERVICE_ID)
            self.assertEqual(row["status"], "active")
            self.assertEqual(row["login_snapshot"], SERVICE_ID)

    def test_unknown_service_id_and_wrong_secret_share_one_error(self):
        with TemporaryDirectory() as directory:
            _database, authentication, _authorization, _context = _build(directory, {SERVICE_ID: SERVICE_SECRET})
            rejections = []
            for service_id, secret in ((SERVICE_ID, "wrong"), ("unknown-bot", SERVICE_SECRET)):
                with self.assertRaises(ApiError) as raised:
                    authentication.exchange_service_credential(service_id, secret, now=100)
                rejections.append((raised.exception.status, raised.exception.code))
        self.assertEqual(rejections, [(401, "service_credential_rejected"), (401, "service_credential_rejected")])

    def test_suspended_service_account_cannot_exchange(self):
        with TemporaryDirectory() as directory:
            database, authentication, _authorization, _context = _build(directory, {SERVICE_ID: SERVICE_SECRET})
            authentication.exchange_service_credential(SERVICE_ID, SERVICE_SECRET, now=100)
            PlatformStore(database, "complex").set_user_status(SERVICE_ID, "suspended", now=200)

            with self.assertRaises(ApiError) as raised:
                authentication.exchange_service_credential(SERVICE_ID, SERVICE_SECRET, now=300)
        self.assertEqual(raised.exception.code, "service_credential_rejected")

    def test_service_session_reads_only_granted_nodes(self):
        with TemporaryDirectory() as directory:
            _database, authentication, authorization_service, _context = _build(directory, {SERVICE_ID: SERVICE_SECRET})
            resources = ResourceRegistry()
            resources.register("system.authentication.me").add_perm(
                "read", lambda data, headers, now: {"user_id": SERVICE_ID, **data},
            )
            match = resources.resolve("read", "system.authentication.me")
            token = authentication.exchange_service_credential(SERVICE_ID, SERVICE_SECRET, now=100).token
            headers = {"authorization": f"Bearer {token}"}

            with self.assertRaises(ApiError) as raised:
                authorize_resource_operation(
                    match, authentication=authentication, authorization=authorization_service,
                    headers=headers, now=200,
                )
            self.assertEqual(raised.exception.code, "permission_denied")

            authorization_service.set_user_system_permissions(
                SERVICE_ID, {"system.authentication.me.read": "allow"}, granted_by="system", now=150,
            )
            actor = authorize_resource_operation(
                match, authentication=authentication, authorization=authorization_service,
                headers=headers, now=200,
            )
        self.assertEqual(actor, SERVICE_ID)


class ServiceExchangeRouteTests(unittest.TestCase):
    @staticmethod
    def _app(directory: str, service_accounts: dict[str, str]):
        _database, authentication, _authorization, context = _build(directory, service_accounts)
        return create_app(ServerInfo("server", "Test", None, {}), authentication, module_context=context)

    def test_http_route_exchanges_credentials_for_a_session(self):
        with TemporaryDirectory() as directory:
            app = self._app(directory, {SERVICE_ID: SERVICE_SECRET})
            status, payload = _http_call(
                app, "POST", "/v1/auth/service/exchange",
                body=json.dumps({"service_id": SERVICE_ID, "secret": SERVICE_SECRET}).encode(),
                headers={"content-type": "application/json"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["github_user_id"], SERVICE_ID)
        self.assertIn("token", payload)
        self.assertIn("expires_at", payload)

    def test_http_route_rejects_unknown_service_id(self):
        with TemporaryDirectory() as directory:
            app = self._app(directory, {SERVICE_ID: SERVICE_SECRET})
            status, payload = _http_call(
                app, "POST", "/v1/auth/service/exchange",
                body=json.dumps({"service_id": "unknown-bot", "secret": SERVICE_SECRET}).encode(),
                headers={"content-type": "application/json"},
            )
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "service_credential_rejected")

    def test_websocket_action_requires_service_id_and_secret(self):
        with TemporaryDirectory() as directory:
            _database, authentication, _authorization, context = _build(directory, {SERVICE_ID: SERVICE_SECRET})
            match = context.resources.resolve("exchange", "system.authentication.service")
            with self.assertRaises(ApiError) as raised:
                match.operation.handler({"service_id": SERVICE_ID}, {}, None)
            self.assertEqual(raised.exception.code, "invalid_request")

            payload = match.operation.handler(
                {"service_id": SERVICE_ID, "secret": SERVICE_SECRET}, {}, 100,
            ).payload
        self.assertEqual(payload["github_user_id"], SERVICE_ID)


if __name__ == "__main__":
    unittest.main()
