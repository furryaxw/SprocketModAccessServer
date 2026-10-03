from __future__ import annotations

from typing import Any

from ..resource_handlers import ok, require_field, team_member_page, timestamp, with_read_prerequisites
from ...core.contracts import ModuleContext
from ...domain.errors import ApiError
from ...domain.resources import SYSTEM_ACCOUNT_ID, SYSTEM_TEAM_ID
from ...infrastructure.events import ResourceChanged
from ...infrastructure.security.request_context import bearer, platform_user, require_context_team


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


def attach(context: ModuleContext) -> None:
    resources = context.resources

    def users(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
        _require_user_permission(context, actor, "read", headers=headers, now=now)
        platform = _service(context, "platform")
        try:
            limit = int(data.get("limit", 100))
            offset = int(data.get("offset", 0))
            login = str(data.get("login", "")).strip() or None
            display_name = str(data.get("display_name", "")).strip() or None
            user_id = str(data.get("user_id", "")).strip() or None
            status = str(data.get("status", "")).strip() or None
            users = platform.users(
                limit=limit,
                offset=offset,
                login=login,
                display_name=display_name,
                user_id=user_id,
                status=status,
            )
            total = platform.count_users(
                login=login,
                display_name=display_name,
                user_id=user_id,
                status=status,
            )
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        return ok({"users": users, "total": total, "limit": limit, "offset": offset})

    def user(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
        _require_user_permission(context, actor, "read", headers=headers, now=now)
        platform = _service(context, "platform")
        user_id = require_field(data, "user_id")
        value = platform.user(user_id)
        if value is None:
            raise ApiError(404, "not_found", "platform user was not found")
        return ok(value)

    def set_permission_template(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
        user_id = require_field(data, "user_id")
        _require_user_permission(context, actor, "manage", user_id=user_id, headers=headers, now=now)
        template_name = require_field(data, "permission_template")
        result = _run_idempotent(
            context,
            scope="system-user-permission-template",
            headers=headers,
            request={"user_id": user_id, "permission_template": template_name},
            operation=lambda: _service(context, "provisioning").set_user_permission_template(
                user_id, template_name, actor=actor, now=timestamp(now),
            ),
            now=now,
        )
        _publish_user_change(context, kind="update", action="set_permission_template", user_id=user_id, data=result)
        return ok(result)

    def manage(data: dict[str, Any], headers: dict[str, str], now: int | None):
        """组合更新用户状态与权限模板，单次请求、单事务完成。

        多字段保存必须后端单次 mutation，不能出现
        "状态已改、模板未改"或反向的半完成结果。状态变化沿用
        user.confirm 确认令牌；权限模板沿用 system-template 分配写入。
        """
        actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
        user_id = require_field(data, "user_id")
        _require_user_permission(context, actor, "manage", user_id=user_id, headers=headers, now=now)
        status = str(data.get("status", "")).strip() or None
        template_name = str(data.get("permission_template", "")).strip() or None
        if status is None and not template_name:
            raise ApiError(400, "invalid_request", "status or permission_template is required")
        if status is not None:
            _consume_user_confirmation(context, data, user_id=user_id, now=now)
        result = _run_idempotent(
            context,
            scope="system-user-profile",
            headers=headers,
            request={"user_id": user_id, "status": status, "permission_template": template_name},
            operation=lambda: _service(context, "provisioning").update_user_profile(
                user_id, status=status, template_name=template_name, actor=actor, now=timestamp(now),
            ),
            now=now,
        )
        _publish_user_change(context, kind="update", action="manage", user_id=user_id, data=result)
        return ok(result)

    def user_system_permissions(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
        authorization_service = _service(context, "authorization_service")
        _require_user_permission(context, actor, "read", headers=headers, now=now)
        user_id = require_field(data, "user_id")
        return ok({
            "user_id": user_id,
            "permissions": authorization_service.user_system_permissions(user_id),
            "assignments": authorization_service.user_permission_assignments(user_id, now=now),
        })

    def set_user_system_permissions(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
        authorization_service = _service(context, "authorization_service")
        user_id = require_field(data, "user_id")
        _require_user_permission(context, actor, "manage", user_id=user_id, headers=headers, now=now)
        payload = data.get("permissions", {})
        if not isinstance(payload, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in payload.items()):
            raise ApiError(400, "invalid_request", "permissions must be an object of value/effect pairs")
        # 动作节点自动补同资源 read（后端不变量，见 with_read_prerequisites）。
        # effect 由提交方给出（allow/deny），补出来的 read 用 allow。
        payload = with_read_prerequisites(context, {
            str(node): str(effect).strip().casefold() for node, effect in payload.items()
        })
        # 分发范围校验：不能把超出自己分发范围的节点写给别人（与分配、模板、Key 发行同一条规则），
        # 否则拿到 manage 路径就能给自己或他人写 `*`。
        authorization_service.require_grantable(
            actor,
            sorted(payload),
            team_id=SYSTEM_TEAM_ID,
            now=now,
        )
        # 模板实例同样是节点：提交里带上模板节点即可，后端在求值时展开成模板成员节点，
        # 所以这里不需要单独的模板参数。
        result = {
            "user_id": user_id,
            "permissions": authorization_service.set_user_system_permissions(
                user_id, payload, granted_by=actor, now=now,
            ),
        }
        _publish_user_change(context, kind="update", node=f"team.{SYSTEM_TEAM_ID}.permission_assignments",
                             action="manage", user_id=user_id, data=result)
        return ok(result)

    def confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
        confirmations = _service(context, "confirmations")
        if confirmations is None:
            raise ApiError(503, "authorization_unavailable", "confirmation service is not configured")
        user_id = require_field(data, "user_id")
        _require_user_permission(context, actor, "manage", user_id=user_id, headers=headers, now=now)
        token, confirmation = confirmations.issue(action=f"user.confirm", target=user_id, created_by=actor, now=now)
        return ok({"confirmation_token": token, "confirmation": confirmation.__dict__})

    def suspend(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
        platform = _service(context, "platform")
        user_id = require_field(data, "user_id")
        _require_user_permission(context, actor, "manage", user_id=user_id, headers=headers, now=now)
        _guard_status_target(user_id, actor)
        _consume_user_confirmation(context, data, user_id=user_id, now=now)
        result = _run_idempotent(
            context,
            scope="system-user-status",
            headers=headers,
            request={"user_id": user_id, "status": "suspended"},
            operation=lambda: _set_status(
                context, platform, user_id, "suspended", "suspend", data, now,
            ),
            now=now,
        )
        return ok(result)

    def activate(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
        platform = _service(context, "platform")
        user_id = require_field(data, "user_id")
        _require_user_permission(context, actor, "manage", user_id=user_id, headers=headers, now=now)
        _guard_status_target(user_id, actor)
        _consume_user_confirmation(context, data, user_id=user_id, now=now)
        result = _run_idempotent(
            context,
            scope="system-user-status",
            headers=headers,
            request={"user_id": user_id, "status": "active"},
            operation=lambda: _set_status(
                context, platform, user_id, "active", "activate", data, now,
            ),
            now=now,
        )
        return ok(result)

    def system_team_users(data: dict[str, Any], headers: dict[str, str], now: int | None):
        """System Team 的成员列表。

        System Team 不参与 team 模块的按 Team 注册，该节点由本模块注册；载荷形状必须与
        `team.<team_id>.users` 一致，否则 Teams 详情的成员表只在这个工作区为空。
        """
        actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
        _require_user_permission(context, actor, "read", headers=headers, now=now)
        return ok(team_member_page(context, SYSTEM_TEAM_ID, data))

    resources.register(f"team.{SYSTEM_TEAM_ID}.users", description="System Team users") \
        .add_perm("read", system_team_users) \
        .add_perm("read_user", user) \
        .add_perm("manage", manage) \
        .add_perm("set_permission_template", set_permission_template) \
        .add_perm("confirm", confirmation) \
        .add_perm("suspend", suspend) \
        .add_perm("activate", activate)
    resources.register("system.users", description="Platform users") \
        .add_perm("read", users) \
        .add_perm("read_user", user) \
        .add_perm("manage", manage) \
        .add_perm("set_permission_template", set_permission_template) \
        .add_perm("confirm", confirmation) \
        .add_perm("suspend", suspend) \
        .add_perm("activate", activate)
    resources.register("system.users.permissions",
                       description="System user permission assignments") \
        .add_perm("read", user_system_permissions) \
        .add_perm("manage", set_user_system_permissions)


def _require_user_permission(
        context: ModuleContext,
        actor: str,
        action: str,
        *,
        user_id: str | None = None,
        headers: dict[str, str],
        now: int | None,
) -> None:
    require_context_team(headers, SYSTEM_TEAM_ID)
    authorization_service = _service(context, "authorization_service")
    if authorization_service is None:
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    if action == "manage" and user_id:
        if user_id == actor:
            return
        if (
                authorization_service.allows(actor, "user.manage", now=now)
                or authorization_service.allows(actor, f"user.{user_id}.manage", now=now)
        ):
            return
    authorization_service.require_system(actor, f"system.users.{action}", now=now)


def _guard_status_target(user_id: str, actor: str) -> None:
    """账户状态变更的目标保护。

    暂停自己的账户会让该会话在下一次授权判定时 `system_denied`，而恢复需要另一个管理员
    且当前实例可能只有这一个管理员；内置 system 账户是保留身份。两者都不接受状态变更。
    """
    if user_id == actor:
        raise ApiError(403, "self_lockout", "your own account status cannot be changed")
    if user_id == SYSTEM_ACCOUNT_ID:
        raise ApiError(403, "system_account", "the built-in system account status cannot be changed")


def _publish_user_change(context: ModuleContext, *, kind: str, action: str, user_id: str,
                         data: dict[str, Any], node: str = "system.users") -> None:
    # 事件携带的列不超过读权限可见的范围：用户列表投影不含 email。
    visible = {key: value for key, value in data.items() if key != "email"}
    context.events.publish(ResourceChanged(
        kind=kind,
        node=node,
        action=action,
        data={"user_id": user_id, **visible},
        team_id=SYSTEM_TEAM_ID,
        user_id=user_id,
    ))


def _set_status(
        context: ModuleContext,
        platform: Any,
        user_id: str,
        status: str,
        action: str,
        data: dict[str, Any],
        now: int | None,
) -> dict[str, object]:
    _consume_user_confirmation(context, data, user_id=user_id, now=now)
    result = platform.set_user_status(user_id, status, now=timestamp(now))
    _publish_user_change(context, kind="update", action=action, user_id=user_id, data=result)
    return result


def _consume_user_confirmation(
        context: ModuleContext,
        data: dict[str, Any],
        *,
        user_id: str,
        now: int | None,
) -> None:
    try:
        _service(context, "confirmations").consume(
            str(data.get("confirmation_token", "")).strip(),
            action="user.confirm",
            target=user_id,
            now=now,
        )
    except ValueError as exc:
        raise ApiError(400, "confirmation_required", str(exc)) from exc
