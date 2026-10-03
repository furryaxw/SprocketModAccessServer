from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from starlette.websockets import WebSocket

from ..domain.permissions import evaluate
from ..domain.permissions import permission_to_node
from ..infrastructure.events.resource_events import ResourceChanged
from ..infrastructure.network import broadcast_payload, json_dumps

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectionState:
    """一条已认证的订阅连接；可见性只看事件本身与当前授权，不保存连接级 Team 上下文。"""

    websocket: WebSocket
    user_id: str | None


class WebSocketHub:
    def __init__(self, *, authorization_service: Any | None = None) -> None:
        self.authorization_service = authorization_service
        self._connections: set[ConnectionState] = set()
        self._lock = asyncio.Lock()

    async def add(self, state: ConnectionState) -> None:
        async with self._lock:
            self._connections.add(state)

    async def remove(self, state: ConnectionState) -> None:
        async with self._lock:
            self._connections.discard(state)

    async def broadcast_resource_changed(self, event: ResourceChanged) -> None:
        snapshot = tuple(self._connections)
        message = broadcast_payload(action=event.action, node=event.node, data=event.data)
        tasks = []
        for state in snapshot:
            if not self._can_see(state, event):
                continue
            tasks.append(state.websocket.send_text(json_dumps(message)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _can_see(self, state: ConnectionState, event: ResourceChanged) -> bool:
        if state.user_id is None:
            return False
        if self.authorization_service is None:
            return False
        timestamp = int(time.time())
        # Team lifecycle events must still reach system administrators after
        # the Team becomes archived, because the selected workspace may need
        # to be cleared on the client.
        if (
                event.node.startswith("team.")
                and event.node.count(".") == 1
                and self.authorization_service.allows(
                    state.user_id, "system.teams.read", now=timestamp,
                )
        ):
            return True
        try:
            # 作用域只看事件自己：事件带 team_id 就按该 Team 判定（一条连接可跨多个 Team 收事件），
            # 系统作用域事件固定按 system 判定，不跟随连接最近一次选中的 Team。
            context = self.authorization_service.context(
                state.user_id,
                team_id=event.team_id,
                now=timestamp,
            )
        except Exception:
            # 广播是尽力而为；判定失败必须留痕，否则客户端只看到"事件再也不来"。
            logger.warning("websocket broadcast authorization failed user_id=%s node=%s",
                           state.user_id, event.node, exc_info=True)
            return False
        # 订阅按读权限放行：能读该资源的人就该知道它变了；没有 read 动作的节点回退到变更权限。
        if evaluate(
                context.permission_assignments,
                permission_to_node(f"{event.node}.read"),
                now=timestamp,
        ).allowed:
            return True
        permission = permission_to_node(f"{event.node}.{event.action}")
        return evaluate(context.permission_assignments, permission, now=timestamp).allowed
