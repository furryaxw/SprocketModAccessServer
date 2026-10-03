from __future__ import annotations

import asyncio
import json
import os
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sprocket_access_server.modules.system.authentication import AuthenticationService
from sprocket_access_server.core.ports import GitHubIdentity
from sprocket_access_server.domain.protocol import ServerInfo
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.network import ResourceRegistry
from sprocket_access_server.infrastructure.security.sessions import SQLiteSessionStore
from sprocket_access_server.modules.permission_assignments.evaluator import AuthorizationService
from sprocket_access_server.modules.system.platform import PlatformStore
from sprocket_access_server.modules.system.provisioning import SystemProvisioning
from sprocket_access_server.presentation.asgi import create_app
from tests.support.v1_http_client import call as http_call


class FakeGitHub:
    def verify_access_token(self, _token: str) -> GitHubIdentity:
        return GitHubIdentity("123", "admin")


class FakeEmailOutbox:
    def __init__(self) -> None:
        self.calls = 0

    def deliver_once(self, _sender) -> None:
        self.calls += 1


@dataclass(frozen=True)
class DataclassResponse:
    node: str
    permissions: frozenset[str]


class AsgiTests(unittest.TestCase):
    @staticmethod
    def call(app, method: str, path: str, *, body: bytes = b"", headers: dict[str, str] | None = None) -> tuple[
        int, dict[str, object]]:
        raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
        messages = [
            {"type": "http.request", "body": body, "more_body": False},
        ]
        sent: list[dict[str, object]] = []

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        asyncio.run(app({"type": "http", "method": method, "path": path, "headers": raw_headers, "query_string": b"",
                         "client": ("test", 1), "server": ("test", 80), "scheme": "http", "http_version": "1.1"},
                        receive, send))
        start = next(item for item in sent if item["type"] == "http.response.start")
        payload = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            decoded = payload.decode("utf-8")
        return int(start["status"]), decoded

    @staticmethod
    def websocket(
        app,
        path: str,
        message: str | list[str],
        headers: dict[str, str] | None = None,
        query_string: bytes = b"",
    ) -> list[dict[str, object]]:
        raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
        sent: list[dict[str, object]] = []
        texts = [message] if isinstance(message, str) else list(message)
        messages = [{"type": "websocket.connect"}]
        messages.extend({"type": "websocket.receive", "text": text} for text in texts)
        messages.append({"type": "websocket.disconnect", "code": 1000})

        async def receive():
            return messages.pop(0) if messages else {"type": "websocket.disconnect"}

        async def send(event):
            sent.append(event)

        async def run():
            scope = {
                "type": "websocket",
                "path": path,
                "headers": raw_headers,
                "query_string": query_string,
                "client": ("test", 1),
                "server": ("test", 80),
                "scheme": "ws",
                "subprotocols": [],
            }
            await app(scope, receive, send)

        asyncio.run(run())
        return sent

    def test_admin_trailing_slash_serves_frontend(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
            server_info = ServerInfo("server", "Test", None, {})
            resources = ResourceRegistry()
            resources.register("system.server_info").add_public("read", lambda _data, _headers, _now: server_info.payload())
            app = create_app(server_info, authentication, module_context=SimpleNamespace(resources=resources))
            status, payload = self.call(app, "GET", "/admin/")
            if (Path(__file__).parents[1] / "admin-ui" / "dist" / "index.html").is_file():
                self.assertEqual(status, 200)
                self.assertIn("<!doctype html>", payload.lower())
            else:
                self.assertEqual(status, 503)
                self.assertEqual(payload["code"], "admin_ui_unavailable")

    def test_root_redirects_to_the_admin_ui(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
            app = create_app(ServerInfo("server", "Test", None, {}), authentication)
            status, headers, _body = http_call(app, "GET", "/")

        self.assertEqual(status, 307)
        self.assertEqual(headers["location"], "/admin/")

    def test_admin_new_vue_routes_serve_frontend_shell(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
            app = create_app(ServerInfo("server", "Test", None, {}), authentication)
            for path in ("/admin/permission-assignments", "/admin/permission-templates"):
                status, payload = self.call(app, "GET", path, headers={"accept": "text/html"})
                if (Path(__file__).parents[1] / "admin-ui" / "dist" / "index.html").is_file():
                    self.assertEqual(status, 200)
                    self.assertIn("<!doctype html>", payload.lower())
                else:
                    self.assertEqual(status, 503)
                    self.assertEqual(payload["code"], "admin_ui_unavailable")

    def test_websocket_server_info_returns_envelope(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
            server_info = ServerInfo("server", "Test", None, {})
            resources = ResourceRegistry()
            resources.register("system.server_info").add_public("read", lambda _data, _headers, _now: server_info.payload())
            app = create_app(server_info, authentication, module_context=SimpleNamespace(resources=resources))
            events = self.websocket(app, "/ws", json.dumps({
                "action": "read",
                "node": "system.server_info",
                "data": {},
                "request_id": "req-1",
            }))
            payload = json.loads(next(item["text"] for item in events if item["type"] == "websocket.send" and "text" in item))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["action"], "read")
            self.assertEqual(payload["node"], "system.server_info")
            self.assertEqual(payload["request_id"], "req-1")

    def test_websocket_serializes_set_values_in_resource_response(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
            server_info = ServerInfo("server", "Test", None, {})
            resources = ResourceRegistry()
            resources.register("system.test").add_public(
                "read",
                lambda _data, _headers, _now: {"permissions": frozenset({"system.users.read"})},
            )
            app = create_app(server_info, authentication, module_context=SimpleNamespace(resources=resources))
            events = self.websocket(app, "/ws", json.dumps({
                "action": "read",
                "node": "system.test",
                "request_id": "set-response",
            }))

        payload = json.loads(next(item["text"] for item in events if item["type"] == "websocket.send"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["permissions"], ["system.users.read"])

    def test_websocket_serializes_dataclass_resource_response(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
            server_info = ServerInfo("server", "Test", None, {})
            resources = ResourceRegistry()
            resources.register("system.test").add_public(
                "read",
                lambda _data, _headers, _now: DataclassResponse(
                    "system.users",
                    frozenset({"system.users.read"}),
                ),
            )
            app = create_app(server_info, authentication, module_context=SimpleNamespace(resources=resources))
            events = self.websocket(app, "/ws", json.dumps({
                "action": "read",
                "node": "system.test",
                "request_id": "dataclass-response",
            }))

        payload = json.loads(next(item["text"] for item in events if item["type"] == "websocket.send"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["value"]["node"], "system.users")
        self.assertEqual(payload["data"]["value"]["permissions"], ["system.users.read"])

    def test_websocket_merges_only_operation_headers_from_envelope(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
            server_info = ServerInfo("server", "Test", None, {})
            resources = ResourceRegistry()

            def read(_data, headers, _now):
                # header 名按大小写不敏感读取，与资源层的约定一致。
                def value_of(name: str) -> str | None:
                    return next(
                        (str(item) for key, item in headers.items() if str(key).casefold() == name),
                        None,
                    )

                return {
                    "idempotency": value_of("idempotency-key"),
                    "authorization": headers.get("Authorization"),
                    "team": value_of("x-team-id"),
                }

            resources.register("system.test").add_public("read", read)
            app = create_app(server_info, authentication, module_context=SimpleNamespace(resources=resources))
            events = self.websocket(app, "/ws", json.dumps({
                "action": "read",
                "node": "system.test",
                "headers": {
                    "Idempotency-Key": "message-key",
                    "Authorization": "Bearer ignored",
                    "X-Team-Id": "per-request-team",
                },
                "request_id": "headers-response",
            }))

        payload = json.loads(next(item["text"] for item in events if item["type"] == "websocket.send"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["idempotency"], "message-key")
        self.assertIsNone(payload["data"]["authorization"])
        self.assertEqual(payload["data"]["team"], "per-request-team")

    def test_websocket_per_request_team_scope_overrides_the_selected_default(self) -> None:
        """连接先 select 一个默认 Team，请求里再带 X-Team-Id 时必须按请求里的作用域执行。"""
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
            server_info = ServerInfo("server", "Test", None, {})
            resources = ResourceRegistry()

            def team_of(_data, headers, _now):
                return {"team": next(
                    (str(value) for key, value in headers.items() if str(key).casefold() == "x-team-id"),
                    None,
                )}

            resources.register("system.test").add_public("read", team_of)
            resources.register("system.authentication.team_context").add_public(
                "select", lambda _data, _headers, _now: {"ok": True},
            )
            app = create_app(server_info, authentication, module_context=SimpleNamespace(resources=resources))
            events = self.websocket(app, "/ws", [
                json.dumps({"action": "select", "node": "system.authentication.team_context",
                            "data": {"team_id": "team-a"}, "request_id": "select-a"}),
                json.dumps({"action": "read", "node": "system.test", "headers": {"X-Team-Id": "team-b"},
                            "request_id": "read-b"}),
            ])

        payloads = [
            json.loads(item["text"]) for item in events
            if item["type"] == "websocket.send" and "text" in item
        ]
        self.assertTrue(all(payload["ok"] for payload in payloads))
        self.assertEqual(payloads[-1]["data"]["team"], "team-b")

    def test_websocket_permission_node_requires_session_permission(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            sessions = SQLiteSessionStore(database, b"pepper")
            platform = PlatformStore(database, "complex")
            provisioning = SystemProvisioning(database, "complex")
            authentication = AuthenticationService(
                FakeGitHub(),
                sessions,
                ownership=database,
                provisioning=provisioning,
            )
            server_info = ServerInfo("server", "Test", None, {})
            resources = ResourceRegistry()
            resources.register("system.authentication.me") \
                .add_perm("read", lambda _data, _headers, _now: {"github_user_id": "123"})
            services = {
                "authentication": authentication,
                "authorization_service": AuthorizationService(database),
            }
            context = SimpleNamespace(resources=resources, service=lambda name: services[name])
            app = create_app(server_info, authentication, module_context=context)

            denied_events = self.websocket(app, "/ws", json.dumps({
                "action": "read",
                "node": "system.authentication.me",
                "request_id": "denied",
            }))
            token = authentication.exchange_github_token("github-token").token
            allowed_events = self.websocket(app, "/ws", json.dumps({
                "action": "read",
                "node": "system.authentication.me",
                "request_id": "allowed",
            }), headers={"authorization": f"Bearer {token}"})

        denied = json.loads(next(item["text"] for item in denied_events if item["type"] == "websocket.send"))
        allowed = json.loads(next(item["text"] for item in allowed_events if item["type"] == "websocket.send"))
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error"]["code"], "invalid_session")
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["data"]["github_user_id"], "123")

    def test_websocket_query_access_token_establishes_authenticated_hub_state(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            sessions = SQLiteSessionStore(database, b"pepper")
            platform = PlatformStore(database, "complex")
            provisioning = SystemProvisioning(database, "complex")
            authentication = AuthenticationService(
                FakeGitHub(),
                sessions,
                ownership=database,
                provisioning=provisioning,
            )
            token = authentication.exchange_github_token("github-token").token
            server_info = ServerInfo("server", "Test", None, {})
            resources = ResourceRegistry()
            resources.register("system.authentication.me") \
                .add_perm("read", lambda _data, _headers, _now: {"github_user_id": "123"})
            services = {
                "authentication": authentication,
                "authorization_service": AuthorizationService(database),
            }
            context = SimpleNamespace(resources=resources, service=lambda name: services[name])
            app = create_app(server_info, authentication, module_context=context)

            events = self.websocket(
                app,
                "/ws",
                json.dumps({
                    "action": "read",
                    "node": "system.authentication.me",
                    "request_id": "query-token",
                }),
                query_string=f"access_token={token}".encode(),
            )

        payload = json.loads(next(item["text"] for item in events if item["type"] == "websocket.send"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["github_user_id"], "123")

    def test_private_server_v1_http_routes_use_resource_contract(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
            server_info = ServerInfo("server", "Test", None, {})
            resources = ResourceRegistry()
            resources.register("system.server_info").add_public("read", lambda _data, _headers, _now:
                                                                server_info.payload())
            resources.register("system.authentication.github").add_public(
                "exchange", lambda _data, _headers, _now: {"token": "session-token", "github_user_id": "123"}
            )
            context = SimpleNamespace(
                resources=resources,
                service=lambda name: {"server_info": server_info}.get(name),
            )
            app = create_app(server_info, authentication, module_context=context)
            info_status, info = self.call(app, "GET", "/v1/server-info")
            exchange_status, exchange = self.call(
                app,
                "POST",
                "/v1/auth/github/exchange",
                body=b'{"access_token":"github-token"}',
                headers={"content-type": "application/json"},
            )
        self.assertEqual(info_status, 200)
        self.assertEqual(info["protocol_version"], 2)
        self.assertEqual(exchange_status, 200)
        self.assertEqual(exchange["token"], "session-token")

    def test_github_callback_continues_through_nonmatching_configured_origins(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
            app = create_app(ServerInfo("server", "Test", None, {}), authentication)
            with patch.dict(
                os.environ,
                {
                    "SMAS_FRONTEND_ORIGIN": (
                        "http://localhost:5173,http://127.0.0.1:5173"
                    )
                },
                clear=False,
            ):
                status, payload = self.call(
                    app,
                    "GET",
                    "/v1/auth/github/callback",
                )

        self.assertEqual(status, 200)
        self.assertIsInstance(payload, str)
        self.assertIn("try{window.opener?.postMessage", payload)
        self.assertIn('"http://localhost:5173"', payload)
        self.assertIn('"http://127.0.0.1:5173"', payload)

    def test_lifespan_shutdown_stops_email_worker_without_cancelling_poll_wait(self) -> None:
        async def scenario() -> FakeEmailOutbox:
            with TemporaryDirectory() as directory:
                database = SQLiteDatabase(Path(directory) / "access.db")
                authentication = AuthenticationService(FakeGitHub(), SQLiteSessionStore(database, b"pepper"))
                outbox = FakeEmailOutbox()
                app = create_app(
                    ServerInfo("server", "Test", None, {}),
                    authentication,
                    email_outbox=outbox,
                    email_sender=object(),
                    email_poll_interval=60,
                )
                async with app.router.lifespan_context(app):
                    await asyncio.sleep(0)
                return outbox

        outbox = asyncio.run(asyncio.wait_for(scenario(), timeout=1))

        self.assertGreaterEqual(outbox.calls, 1)


if __name__ == "__main__":
    unittest.main()
