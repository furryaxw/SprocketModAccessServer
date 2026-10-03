from __future__ import annotations

import hashlib
from typing import Any

from ..resource_handlers import ok, refresh_permissions, require_field, team_member_page, timestamp
from ...core.contracts import ModuleContext
from ...domain.errors import ApiError
from ...domain.resources import SYSTEM_TEAM_ID, TEMPLATE_TEAM_ID
from ...infrastructure.events import ResourceChanged
from ...infrastructure.security.request_context import platform_user, require_context_team

RESERVED_TEAM_IDS = frozenset({SYSTEM_TEAM_ID, TEMPLATE_TEAM_ID})


def _service(context: ModuleContext, name: str) -> Any:
    return context.service(name)


def _request_key(headers: dict[str, str]) -> str:
    return next(
        (
            str(value).strip()
            for key, value in headers.items()
            if str(key).casefold() == "idempotency-key"
        ),
        "",
    )


def _run_idempotent(
        context: ModuleContext,
        *,
        scope: str,
        headers: dict[str, str],
        request: dict[str, object],
        operation,
        now: int | None,
) -> dict[str, Any]:
    idempotency = _service(context, "idempotency")
    request_key = _request_key(headers)
    if idempotency is None or not request_key:
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
    return idempotency.run(
        scope=scope,
        request_key=request_key,
        request=request,
        operation=operation,
        now=now,
    )


def register_team_resources(context: ModuleContext, team_id: str) -> None:
    resources = context.resources
    if resources.resolve("read", f"team.{team_id}") is not None:
        return

    def team(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_team_admin(context, headers, team_id, now=now)
        value = next((item for item in _service(context, "platform").teams() if item["team_id"] == team_id), None)
        if value is None:
            raise ApiError(404, "not_found", "Team was not found")
        return ok(value)

    def update_details(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = _require_team_admin(context, headers, team_id, now=now)
        platform = _service(context, "platform")
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()
        updated = _run_idempotent(
            context,
            scope="team-details",
            headers=headers,
            request={"team_id": team_id, "name": name, "description": description},
            operation=lambda: _update_team_details(
                context, platform, team_id, name, description, now,
            ),
            now=now,
        )
        return ok(updated)

    def confirmation(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = _require_team_admin(context, headers, team_id, now=now)
        confirmations = _service(context, "confirmations")
        token, confirmation_obj = confirmations.issue(action="team.confirm", target=team_id, created_by=actor, now=now)
        return ok({"confirmation_token": token, "confirmation": confirmation_obj.__dict__})

    def suspend(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_team_status(context, headers, team_id, "suspend", now=now)
        result = _run_idempotent(
            context,
            scope="team-status",
            headers=headers,
            request={"team_id": team_id, "status": "suspended"},
            operation=lambda: _change_team_status(
                context, _data, headers, team_id=team_id, status="suspended", now=now,
            ),
            now=now,
        )
        return ok(result)

    def archive(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_team_status(context, headers, team_id, "archive", now=now)
        result = _run_idempotent(
            context,
            scope="team-status",
            headers=headers,
            request={"team_id": team_id, "status": "archived"},
            operation=lambda: _change_team_status(
                context, _data, headers, team_id=team_id, status="archived", now=now,
            ),
            now=now,
        )
        return ok(result)

    def activate(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_team_status(context, headers, team_id, "activate", now=now)
        result = _run_idempotent(
            context,
            scope="team-status",
            headers=headers,
            request={"team_id": team_id, "status": "active"},
            operation=lambda: _change_team_status(
                context, _data, headers, team_id=team_id, status="active", now=now,
            ),
            now=now,
        )
        return ok(result)

    def team_users(data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_team_user_read(context, headers, team_id, now=now)
        return ok(team_member_page(context, team_id, data))

    def invite(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = _require_team_invite(context, headers, team_id, now=now)
        platform = _service(context, "platform")
        target_user = require_field(data, "user_id")
        template_name = str(data.get("permission_template", "")).strip()
        result = _run_idempotent(
            context,
            scope="team-invite",
            headers=headers,
            request={
                "team_id": team_id,
                "user_id": target_user,
                "permission_template": template_name,
            },
            operation=lambda: _invite_member(
                context, platform, team_id, target_user, template_name, actor, now,
            ),
            now=now,
        )
        return ok(result)

    def member_confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = _require_team_admin(context, headers, team_id, now=now)
        user_id = require_field(data, "user_id")
        token, confirmation_obj = _service(context, "confirmations").issue(
            action="team.member.confirm",
            target=f"{team_id}:{user_id}",
            created_by=actor,
            now=now,
        )
        return ok({"confirmation_token": token, "confirmation": confirmation_obj.__dict__})

    def change_member_permission_template(data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_team_admin(context, headers, team_id, now=now)
        user_id = require_field(data, "user_id")
        template_name = require_field(data, "permission_template")
        result = _run_idempotent(
            context,
            scope="team-member-template",
            headers=headers,
            request={"team_id": team_id, "user_id": user_id, "permission_template": template_name},
            operation=lambda: _set_member_template(context, team_id, user_id, template_name, now),
            now=now,
        )
        return ok(result)

    def remove_member(data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_team_admin(context, headers, team_id, now=now)
        user_id = require_field(data, "user_id")
        result = _run_idempotent(
            context,
            scope="team-member-remove",
            headers=headers,
            request={"team_id": team_id, "user_id": user_id},
            operation=lambda: _remove_member(context, team_id, user_id, now),
            now=now,
        )
        return ok(result)

    resources.register(f"team.{team_id}", description="Team detail and settings") \
        .add_perm("read", team) \
        .add_perm("manage", update_details)
    resources.register(f"team.{team_id}.confirmation", description="Team confirmation") \
        .add_perm("confirm", confirmation)
    resources.register(f"team.{team_id}.status", description="Team status") \
        .add_perm("suspend", suspend) \
        .add_perm("activate", activate) \
        .add_perm("archive", archive)
    resources.register(f"team.{team_id}.users", description="Team users") \
        .add_perm("read", team_users) \
        .add_perm("invite", invite) \
        .add_perm("manage", change_member_permission_template) \
        .add_perm("remove", remove_member)
    refresh_permissions(context)


def register_team_mutation(context: ModuleContext, team_id: str, kind: str, data: dict[str, Any] | None = None) -> None:
    context.events.publish(
        ResourceChanged(kind=kind, node=f"team.{team_id}", action="manage", data=data or {}, team_id=team_id))


def attach(context: ModuleContext) -> None:
    resources = context.resources
    platform = context.services.get("platform")

    def teams(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_system_team_read(context, headers, now=now)
        return ok({"teams": platform.teams()})

    def team(data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_system_team_read(context, headers, now=now)
        team_id = require_field(data, "team_id")
        value = next((item for item in platform.teams() if item["team_id"] == team_id), None)
        if value is None:
            raise ApiError(404, "not_found", "Team was not found")
        return ok(value)

    def update_details(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = _require_system_team_manage(context, headers, now=now)
        team_id = require_field(data, "team_id")
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()
        result = _run_idempotent(
            context,
            scope="team-details",
            headers=headers,
            request={"team_id": team_id, "name": name, "description": description},
            operation=lambda: _update_team_details(
                context, _service(context, "platform"), team_id, name, description, now,
            ),
            now=now,
        )
        return ok(result)

    def confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = _require_system_team_manage(context, headers, now=now)
        team_id = require_field(data, "team_id")
        token, confirmation_obj = _service(context, "confirmations").issue(
            action="team.confirm",
            target=team_id,
            created_by=actor,
            now=now,
        )
        return ok({"confirmation_token": token, "confirmation": confirmation_obj.__dict__})

    def suspend(data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_system_team_manage(context, headers, now=now)
        team_id = require_field(data, "team_id")
        result = _run_idempotent(
            context,
            scope="team-status",
            headers=headers,
            request={"team_id": team_id, "status": "suspended"},
            operation=lambda: _change_team_status(
                context, data, headers, team_id=team_id, status="suspended", now=now,
            ),
            now=now,
        )
        return ok(result)

    def archive(data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_system_team_manage(context, headers, now=now)
        team_id = require_field(data, "team_id")
        result = _run_idempotent(
            context,
            scope="team-status",
            headers=headers,
            request={"team_id": team_id, "status": "archived"},
            operation=lambda: _change_team_status(
                context, data, headers, team_id=team_id, status="archived", now=now,
            ),
            now=now,
        )
        return ok(result)

    def activate(data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_system_team_manage(context, headers, now=now)
        team_id = require_field(data, "team_id")
        result = _run_idempotent(
            context,
            scope="team-status",
            headers=headers,
            request={"team_id": team_id, "status": "active"},
            operation=lambda: _change_team_status(
                context, data, headers, team_id=team_id, status="active", now=now,
            ),
            now=now,
        )
        return ok(result)

    resources.register("system.teams", description="Platform Team directory") \
        .add_perm("read", teams) \
        .add_perm("read_team", team) \
        .add_perm("manage", update_details) \
        .add_perm("confirm", confirmation) \
        .add_perm("suspend", suspend) \
        .add_perm("activate", activate) \
        .add_perm("archive", archive)

    def accept_invitation(data: dict[str, Any], headers: dict[str, str], now: int | None):
        """接受 Team 邀请：邀请令牌即凭据，接受者不需要先属于该 Team。"""
        actor, _ = platform_user(
            _service(context, "authentication"), _service(context, "platform"), headers, now=now,
        )
        token = require_field(data, "token")

        def operation() -> dict[str, object]:
            try:
                result = _service(context, "provisioning").accept_invitation(
                    token, actor, now=timestamp(now),
                )
            except ValueError as exc:
                raise ApiError(400, "invitation_invalid", str(exc)) from exc
            _publish_team_change(
                context,
                kind="update",
                team_id=str(result.get("team_id", "")),
                action="invite",
                data=result,
            )
            return result

        # 同一令牌重放（网络重试）必须返回同一结果，而不是"邀请已失效"。
        return _run_idempotent(
            context,
            scope="team-invitation-accept",
            headers=headers,
            request={"token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(), "user_id": actor},
            operation=operation,
            now=now,
        )

    resources.register("system.team_invitations", description="Team invitations") \
        .add_public("accept", accept_invitation)

    for item in platform.teams() if platform is not None else []:
        team_id = str(item["team_id"])
        if team_id == SYSTEM_TEAM_ID:
            continue
        register_team_resources(context, team_id)

    def on_team_created(event: ResourceChanged) -> None:
        if event.kind != "create" or not event.node.startswith("team."):
            return
        team_id = str(event.team_id or event.node.removeprefix("team.")).strip()
        if not team_id or "." in team_id or team_id == SYSTEM_TEAM_ID:
            return
        with resources.module_scope():
            register_team_resources(context, team_id)
        refresh_permissions(context)

    context.events.subscribe(ResourceChanged, on_team_created)


def _require_system_team_read(context: ModuleContext, headers: dict[str, str], *, now: int | None) -> str:
    require_context_team(headers, SYSTEM_TEAM_ID)
    actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
    authorization_service = _service(context, "authorization_service")
    if authorization_service is None:
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    authorization_service.require_system(actor, "system.teams.read", now=now)
    return actor


def _require_system_team_manage(context: ModuleContext, headers: dict[str, str], *, now: int | None) -> str:
    require_context_team(headers, SYSTEM_TEAM_ID)
    actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
    authorization_service = _service(context, "authorization_service")
    if authorization_service is None:
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    authorization_service.require_system(actor, "system.teams.manage", now=now)
    return actor


def _require_team_admin(context: ModuleContext, headers: dict[str, str], team_id: str, *, now: int | None) -> str:
    actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
    authorization_service = _service(context, "authorization_service")
    if authorization_service is None:
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    authorization_service.require_system_or_team(
        actor,
        "system.teams.manage",
        team_id,
        "users.manage",
        now=now,
    )
    return actor


def _require_team_invite(context: ModuleContext, headers: dict[str, str], team_id: str, *,
                         now: int | None) -> str:
    """邀请成员按 permission-tree §4.4 的 `Invite` 节点授权。

    System 级管理员仍可用 `system.teams.manage` 邀请；成员模板变更与移除的处理器
    未注册为资源节点，使用 `_require_team_admin`。
    """
    actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
    authorization_service = _service(context, "authorization_service")
    if authorization_service is None:
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    authorization_service.require_system_or_team(
        actor,
        "system.teams.manage",
        team_id,
        "users.invite",
        now=now,
    )
    return actor


def _require_team_status(
        context: ModuleContext,
        headers: dict[str, str],
        team_id: str,
        action: str,
        *,
        now: int | None,
) -> str:
    """Team 生命周期变更按 `team.<team_id>.status.<action>` 授权。

    这一节点是权限树 §4.3 的契约（Admin 模板持有 suspend/archive），因此不再要求
    `users.manage`——否则模板授权的角色点下去必然失败。
    """
    actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
    authorization_service = _service(context, "authorization_service")
    if authorization_service is None:
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    authorization_service.require_system_or_team(
        actor,
        "system.teams.manage",
        team_id,
        f"status.{action}",
        now=now,
    )
    return actor


def _require_team_user_read(
        context: ModuleContext,
        headers: dict[str, str],
        team_id: str,
        *,
        now: int | None,
) -> str:
    actor, _ = platform_user(
        _service(context, "authentication"),
        _service(context, "platform"),
        headers,
        now=now,
    )
    authorization_service = _service(context, "authorization_service")
    if authorization_service is None:
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    authorization_service.require_system_or_team(
        actor,
        "system.teams.manage",
        team_id,
        "users.read",
        now=now,
    )
    return actor


def _publish_team_change(context: ModuleContext, *, kind: str, team_id: str, action: str, data: dict[str, Any]) -> None:
    context.events.publish(
        ResourceChanged(kind=kind, node=f"team.{team_id}", action=action, data=data, team_id=team_id))


def _update_team_details(
        context: ModuleContext,
        platform: Any,
        team_id: str,
        name: str,
        description: str,
        now: int | None,
) -> dict[str, object]:
    result = platform.update_team(
        team_id,
        name=name,
        description=description,
        now=timestamp(now),
    )
    _publish_team_change(context, kind="update", team_id=team_id, action="manage", data=result)
    return result


def _invite_member(
        context: ModuleContext,
        platform: Any,
        team_id: str,
        target_user: str,
        template_name: str,
        actor: str,
        now: int | None,
) -> dict[str, object]:
    result = platform.invite(
        team_id, target_user, template_name, actor, now=timestamp(now),
    )
    # 事件是通知而不是凭据载体：邀请令牌只回给调用方，订阅方拿不到。
    event_data = {key: value for key, value in result.items() if key != "token"}
    _publish_team_change(context, kind="create", team_id=team_id, action="invite", data=event_data)
    return result


def _set_member_template(
        context: ModuleContext,
        team_id: str,
        user_id: str,
        template_name: str,
        now: int | None,
) -> dict[str, object]:
    result = _service(context, "provisioning").set_member_permission_template(
        team_id, user_id, template_name, now=timestamp(now),
    )
    _publish_team_change(context, kind="update", team_id=team_id, action="manage_member", data=result)
    return result


def _remove_member(context: ModuleContext, team_id: str, user_id: str, now: int | None) -> dict[str, object]:
    result = _service(context, "provisioning").remove_member(team_id, user_id, now=timestamp(now))
    _publish_team_change(context, kind="update", team_id=team_id, action="remove_member", data=result)
    return result


def _change_team_status(
        context: ModuleContext,
        data: dict[str, Any],
        headers: dict[str, str],
        *,
        team_id: str,
        status: str,
        now: int | None,
) -> dict[str, object]:
    # 保留 Team 承载工作区与模板实例：暂停/归档后没有把它恢复为 active 的接口，
    # 因此状态变更在这里统一拒绝。
    if team_id in RESERVED_TEAM_IDS:
        raise ApiError(403, "reserved_team", "reserved Team status cannot be changed")
    _consume_team_confirmation(
        context, data, headers, team_id=team_id, now=now,
    )
    result = _service(context, "platform").set_team_status(
        team_id, status, now=timestamp(now),
    )
    _publish_team_change(context, kind="update", team_id=team_id, action="manage", data=result)
    return result


def _consume_team_confirmation(
        context: ModuleContext,
        data: dict[str, Any],
        headers: dict[str, str],
        *,
        team_id: str,
        now: int | None,
) -> None:
    token = str(data.get("confirmation_token", "")).strip()
    if not token:
        token = next(
            (str(value).strip() for key, value in headers.items()
             if str(key).casefold() == "x-confirmation-token"),
            "",
        )
    try:
        _service(context, "confirmations").consume(
            token,
            action="team.confirm",
            target=team_id,
            now=now,
        )
    except ValueError as exc:
        raise ApiError(400, "confirmation_required", str(exc)) from exc
