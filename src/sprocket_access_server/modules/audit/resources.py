from __future__ import annotations

from typing import Any

from ..resource_handlers import ok, refresh_permissions
from ...core.contracts import ModuleContext
from ...domain.errors import ApiError
from ...domain.resources import SYSTEM_TEAM_ID
from ...infrastructure.events import ResourceChanged
from ...infrastructure.security.request_context import platform_user, require_context_team


def _service(context: ModuleContext, name: str) -> Any:
    return context.service(name)


def _optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def attach(context: ModuleContext) -> None:
    resources = context.resources

    def register_team_audit(team_id: str) -> None:
        node = f"team.{team_id}.audit"
        if resources.resolve("read", node) is not None:
            # 注册必须幂等：team 创建事件之外，邀请成员也会 publish
            # kind="create" 的 team 事件，重复注册会让该请求以
            # "duplicate resource operation" 失败。
            return

        def search(data: dict[str, Any], headers: dict[str, str], now: int | None):
            actor, _ = platform_user(
                _service(context, "authentication"),
                _service(context, "platform"),
                headers,
                now=now,
            )
            authorization = _service(context, "authorization_service")
            if team_id == SYSTEM_TEAM_ID:
                # System Team 审计覆盖跨 Team 事件。
                require_context_team(headers, SYSTEM_TEAM_ID)
            authorization.require_team(actor, team_id, "audit.read", now=now)
            audit = _service(context, "audit")
            try:
                limit = int(data.get("limit", 100))
                offset = int(data.get("offset", 0))
                events = audit.search(
                    actor=_optional(data.get("actor")),
                    action=_optional(data.get("action")),
                    target=_optional(data.get("target")),
                    query=_optional(data.get("query")),
                    limit=limit,
                    offset=offset,
                    team_id=None if team_id == SYSTEM_TEAM_ID else team_id,
                )
                total = audit.count(
                    actor=_optional(data.get("actor")),
                    action=_optional(data.get("action")),
                    target=_optional(data.get("target")),
                    query=_optional(data.get("query")),
                    team_id=None if team_id == SYSTEM_TEAM_ID else team_id,
                )
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "invalid_request", str(exc)) from exc
            return ok({
                "events": events,
                "total": total,
                "limit": limit,
                "offset": offset,
                "team_id": team_id,
                "workspace_kind": "system" if team_id == SYSTEM_TEAM_ID else "team",
            })

        def export(data: dict[str, Any], headers: dict[str, str], now: int | None):
            actor, _ = platform_user(
                _service(context, "authentication"),
                _service(context, "platform"),
                headers,
                now=now,
            )
            authorization = _service(context, "authorization_service")
            if team_id == SYSTEM_TEAM_ID:
                require_context_team(headers, SYSTEM_TEAM_ID)
            authorization.require_team(actor, team_id, "audit.export", now=now)
            audit = _service(context, "audit")
            try:
                limit = int(data.get("limit", 5000))
                csv_text = audit.export_csv(
                    actor=_optional(data.get("actor")),
                    action=_optional(data.get("action")),
                    target=_optional(data.get("target")),
                    limit=limit,
                    team_id=None if team_id == SYSTEM_TEAM_ID else team_id,
                )
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "invalid_request", str(exc)) from exc
            return ok({"csv": csv_text, "team_id": team_id})

        resources.register(f"team.{team_id}.audit", description="Team audit events") \
            .add_perm("read", search) \
            .add_perm("export", export)
    register_team_audit(SYSTEM_TEAM_ID)
    # attach 阶段 platform 可能尚未注入（部分测试组装顺序），缺失时跳过
    # 存量 Team 预注册，由 team-create 事件补齐。
    platform = context.services.get("platform")
    for item in platform.teams() if platform is not None else []:
        team_id = str(item["team_id"])
        if team_id == SYSTEM_TEAM_ID:
            continue
        register_team_audit(team_id)

    def on_team_created(event: ResourceChanged) -> None:
        if event.kind != "create" or not str(event.node or "").startswith("team."):
            return
        team_id = str(event.team_id or event.node.removeprefix("team.")).strip()
        if not team_id or "." in team_id or team_id == SYSTEM_TEAM_ID:
            return
        with resources.module_scope():
            register_team_audit(team_id)
        refresh_permissions(context)

    context.events.subscribe(ResourceChanged, on_team_created)