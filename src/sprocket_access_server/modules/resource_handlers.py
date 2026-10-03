from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from ..domain.errors import ApiError
from ..domain.permissions import permission_to_node
from ..infrastructure.events import ResourceChanged


def json_body(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8") if data else b""


def ok(payload: dict[str, Any]) -> Any:
    from .http_common import ApiResponse

    return ApiResponse(200, payload)


def query_path(base: str, data: dict[str, Any]) -> str:
    query = {
        key: value
        for key, value in data.items()
        if value is not None and not isinstance(value, (dict, list))
    }
    if not query:
        return base
    return f"{base}?{urlencode(query)}"


def require_field(data: dict[str, Any], name: str) -> str:
    value = str(data.get(name, "")).strip()
    if not value:
        raise ApiError(400, "invalid_request", f"{name} is required")
    return value


def timestamp(now: int | None) -> int:
    return int(time.time()) if now is None or now <= 0 else now


def refresh_permissions(context: Any) -> None:
    """重建权限目录：注册表节点 + 模板实例节点。

    每个模板实例注册成独立资源（auto_grant），于是目录里同时有
    `team.<team>.templates.<template-id>` 与 `team.<team>.templates.<template-id>.grant`
    —— 前者是模板权限集，后者是"允许把这个模板分配给其他人"。前端只渲染这份目录。
    """
    permissions = context.service("permissions")
    database = context.services.get("database") if hasattr(context, "services") else None
    nodes = set(context.resources.permission_nodes())
    if database is not None:
        from .permission_templates.instance_nodes import all_instance_nodes

        # 注册决定 `.grant` 的存在（auto_grant 资源会派生 `<node>.grant`），而实例节点
        # 自身来自数据，需要显式并进目录：目录 = 注册派生节点 ∪ 模板实例节点。
        for node in all_instance_nodes(database):
            context.resources.register(
                node,
                description="Permission template instance",
                auto_grant=True,
            )
            nodes.add(node)
        nodes.update(context.resources.permission_nodes())
    permissions.permission_nodes = tuple(sorted(nodes))


def team_member_page(context: Any, team_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """`team.<team_id>.users` read 的统一载荷。

    该节点由 team 与 users 两个模块分别注册（System Team 归 users），两侧必须给出同一
    形状：成员表按 `members` 渲染，载荷键不一致时该工作区的成员列表恒为空。
    """
    try:
        limit = int(data.get("limit", 100))
        offset = int(data.get("offset", 0))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "invalid_request", "user pagination is invalid") from exc
    if not 1 <= limit <= 200 or offset < 0:
        raise ApiError(400, "invalid_request", "user pagination is invalid")
    values = context.service("authorization_service").team_members(team_id)
    filters = (
        ("login_snapshot", data.get("login")),
        ("display_name", data.get("display_name")),
        ("github_user_id", data.get("user_id")),
    )
    for field, raw in filters:
        value = str(raw or "").strip().casefold()
        if value:
            values = [
                item for item in values
                if value in str(item.get(field) or "").casefold()
            ]
    status = str(data.get("status", "")).strip()
    if status:
        if status not in {"active", "suspended"}:
            raise ApiError(400, "invalid_request", "user status is invalid")
        values = [item for item in values if item.get("status") == status]
    return {
        "members": values[offset:offset + limit],
        "total": len(values),
        "limit": limit,
        "offset": offset,
    }


def resource_grant_node(context: Any, permission: str) -> str | None:
    """解析"分配该权限需要哪个委派节点"。

    普通节点取最长的已注册前缀资源（auto_grant）的 `.grant`；模板实例节点取它自己的
    `.grant`（`team.<team>.templates.<id>.grant`），即"允许把这个模板分配给其他人"。
    """
    from .permission_templates.instance_nodes import is_template_node

    normalized = permission_to_node(permission)
    if is_template_node(normalized):
        return f"{normalized}.grant"
    candidates: list[tuple[int, str]] = []
    for definition in context.resources.definitions():
        if definition.node == normalized:
            continue
        if normalized.startswith(f"{definition.node}.") and definition.auto_grant:
            candidates.append((len(definition.node), f"{definition.node}.grant"))
    return max(candidates, default=(0, None))[1]


def with_read_prerequisites(context: Any, values: Mapping[str, str]) -> dict[str, str]:
    """动作节点自动补同一资源的 `read` 前置节点（effect 为 `allow`）。

    规则属于后端不变量：只有同资源的 `read` 也在目录里时才补，`*` 与末段通配符不补。
    客户端不自行补节点，避免"换个客户端就没有该规则"。
    """
    permissions = context.services.get("permissions") if hasattr(context, "services") else None
    available = set(permissions.permission_nodes) if permissions is not None else set()
    database = context.services.get("database") if hasattr(context, "services") else None
    if database is not None:
        with database.transaction() as connection:
            available.update(
                str(row["node"])
                for row in connection.execute(
                    "SELECT node FROM permission_nodes WHERE active=1"
                ).fetchall()
            )
    result = {permission_to_node(node): effect for node, effect in values.items()}
    for value in list(result):
        if value == "*" or value.endswith(".*") or value.endswith(".read"):
            continue
        separator = value.rfind(".")
        if separator <= 0:
            continue
        prerequisite = f"{value[:separator]}.read"
        if prerequisite in available:
            result.setdefault(prerequisite, "allow")
    return result


def publish_change(
        context: Any,
        *,
        kind: str,
        node: str,
        action: str,
        data: dict[str, Any] | None = None,
        team_id: str | None = None,
        user_id: str | None = None,
) -> None:
    context.service("events").publish(
        ResourceChanged(kind=kind, node=node, action=action, data=data or {}, team_id=team_id, user_id=user_id)
    )
