from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from sprocket_access_server.domain.models import PermissionAssignment
from sprocket_access_server.infrastructure.events import ResourceChanged
from sprocket_access_server.presentation.ws_hub import ConnectionState, WebSocketHub


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, message: str) -> None:
        self.messages.append(message)


class MutableAuthorization:
    def __init__(self) -> None:
        self.assignments: tuple[PermissionAssignment, ...] = ()

    def context(self, user_id: str, *, team_id: str | None = None, now: int | None = None):
        return SimpleNamespace(user_id=user_id, team_id=team_id, permission_assignments=self.assignments)


class WebSocketHubTests(unittest.TestCase):
    def test_read_permission_receives_change_events(self) -> None:
        """订阅按读权限放行：只有 read 的连接也要收到变更通知。"""
        async def run() -> tuple[list[str], list[str]]:
            authorization = MutableAuthorization()
            reader = FakeWebSocket()
            stranger = FakeWebSocket()
            hub = WebSocketHub(authorization_service=authorization)
            event = ResourceChanged(
                kind="update",
                node="team.default.permission_assignments",
                action="manage",
                data={"grant_id": "grant-1", "permissions": ["team.default.packages.read"]},
                team_id="default",
            )
            await hub.add(ConnectionState(websocket=reader, user_id="123"))
            authorization.assignments = (
                PermissionAssignment("assignment-1", "team.default.permission_assignments.read"),
            )
            await hub.broadcast_resource_changed(event)
            reader_messages = list(reader.messages)

            authorization.assignments = (PermissionAssignment("assignment-2", "team.default.packages.read"),)
            await hub.add(ConnectionState(websocket=stranger, user_id="456"))
            await hub.broadcast_resource_changed(event)
            return reader_messages, list(stranger.messages)

        reader_messages, stranger_messages = asyncio.run(run())

        self.assertEqual(len(reader_messages), 1)
        self.assertEqual(len(stranger_messages), 0)

    def test_broadcast_visibility_uses_current_authorization_context(self) -> None:
        async def run() -> list[str]:
            authorization = MutableAuthorization()
            websocket = FakeWebSocket()
            hub = WebSocketHub(authorization_service=authorization)
            state = ConnectionState(websocket=websocket, user_id="123")
            event = ResourceChanged(
                kind="update",
                node="team.default.packages",
                action="manage",
                data={"package_id": "example.mod"},
                team_id="default",
            )
            await hub.add(state)
            await hub.broadcast_resource_changed(event)
            authorization.assignments = (
                PermissionAssignment("assignment-1", "team.default.packages.manage"),
            )
            await hub.broadcast_resource_changed(event)
            return websocket.messages

        messages = asyncio.run(run())

        self.assertEqual(len(messages), 1)
        payload = json.loads(messages[0])
        self.assertEqual(payload["action"], "manage")
        self.assertEqual(payload["node"], "team.default.packages")


if __name__ == "__main__":
    unittest.main()
