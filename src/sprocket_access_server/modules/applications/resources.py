from __future__ import annotations

from typing import Any

from ..resource_handlers import require_field, timestamp
from ..team.resources import register_team_resources
from ...core.contracts import ModuleContext
from ...domain.errors import ApiError
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
):
    idempotency = _service(context, "idempotency")
    key = _request_key(headers)
    if idempotency is None or not key:
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
    return idempotency.run(
        scope=scope,
        request_key=key,
        request=request,
        operation=operation,
        now=now,
    )


def attach(context: ModuleContext) -> None:
    def create(data: dict[str, Any], headers: dict[str, str], now: int | None):
        _require_system_team_permission(context, headers, "system.team_applications.create", now=now)
        platform = _service(context, "platform")
        actor, _ = platform_user(
            _service(context, "authentication"),
            _service(context, "platform"),
            headers,
            now=now,
        )
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()

        def operation() -> dict[str, object]:
            result = platform.create_application(actor, name, description, now=timestamp(now))
            context.events.publish(ResourceChanged(
                kind="create",
                node="system.team_applications",
                action="create",
                data=result,
                user_id=actor,
            ))
            return result

        return _run_idempotent(
            context,
            scope="team-application-create",
            headers=headers,
            request={"name": name, "description": description},
            operation=operation,
            now=now,
        )

    def read(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = _require_system_team_permission(context, headers, "system.team_applications.read", now=now)
        try:
            limit = int(data.get("limit", 50))
            offset = int(data.get("offset", 0))
            applications, total = _service(context, "platform").applications(
                limit=limit, offset=offset,
            )
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        # 审核权限看全部待审申请；只有提交权限的账户只看自己提交的，避免申请人互相可见。
        if not _is_reviewer(context, actor, now=now):
            applications = [item for item in applications if str(item.get("applicant_user_id", "")) == actor]
            total = len(applications)
        return {
            "applications": applications,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def approve(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = _require_system_team_permission(
            context, headers, "system.team_applications.approve", now=now
        )
        application_id = require_field(data, "application_id")
        _consume_application_confirmation(
            context, data, application_id=application_id, operation="approve", now=now,
        )

        def operation() -> dict[str, object]:
            result = _service(context, "provisioning").approve_application(
                application_id, actor, now=timestamp(now)
            )
            team_id = result.get("team_id")
            if isinstance(team_id, str) and team_id:
                with context.resources.module_scope():
                    register_team_resources(context, team_id)
                context.events.publish(
                    ResourceChanged(
                        kind="create",
                        node=f"team.{team_id}",
                        action="manage",
                        data=result,
                        team_id=team_id,
                    )
                )
            return result

        return _run_idempotent(
            context,
            scope="team-application-approve",
            headers=headers,
            request={"application_id": application_id},
            operation=operation,
            now=now,
        )

    def reject(data: dict[str, Any], headers: dict[str, str], now: int | None):
        platform = _service(context, "platform")
        actor = _require_system_team_permission(
            context, headers, "system.team_applications.reject", now=now
        )
        application_id = require_field(data, "application_id")
        reason = str(data.get("reason", "")).strip()
        _consume_application_confirmation(
            context, data, application_id=application_id, operation="reject", now=now,
        )

        def operation() -> dict[str, object]:
            result = platform.reject_application(application_id, actor, reason, now=timestamp(now))
            context.events.publish(ResourceChanged(
                kind="update",
                node="system.team_applications",
                action="reject",
                data=result,
                user_id=str(result.get("applicant_user_id", "")) or None,
            ))
            return result

        return _run_idempotent(
            context,
            scope="team-application-reject",
            headers=headers,
            request={"application_id": application_id, "reason": reason},
            operation=operation,
            now=now,
        )

    def confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None):
        application_id = require_field(data, "application_id")
        operation = str(data.get("operation", "approve")).strip()
        if operation not in {"approve", "reject"}:
            raise ApiError(400, "invalid_request", "application confirmation operation is invalid")
        actor = _require_system_team_permission(
            context, headers, f"system.team_applications.{operation}", now=now
        )
        token, confirmation_obj = _service(context, "confirmations").issue(
            action=f"team.application.{operation}.confirm",
            target=application_id,
            created_by=actor,
            now=now,
        )
        result: dict[str, object] = {"confirmation_token": token, "confirmation": confirmation_obj.__dict__}
        if operation == "approve":
            try:
                result["preview"] = _service(context, "provisioning").preview_application_approval(application_id)
            except ValueError as exc:
                raise ApiError(400, "invalid_request", str(exc)) from exc
        return result

    context.resources.register("system.team_applications", description="Team applications") \
        .add_perm("read", read) \
        .add_perm("create", create) \
        .add_perm("approve", approve) \
        .add_perm("reject", reject) \
        .add_perm("confirm", confirmation)


def _require_system_team_permission(
        context: ModuleContext,
        headers: dict[str, str],
        permission: str,
        *,
        now: int | None,
) -> str:
    require_context_team(headers, "system")
    actor, _ = platform_user(
        _service(context, "authentication"),
        _service(context, "platform"),
        headers,
        now=now,
    )
    authorization_service = _service(context, "authorization_service")
    if authorization_service is None:
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    authorization_service.require_system(actor, permission, now=now)
    return actor


def _is_reviewer(context: ModuleContext, actor: str, *, now: int | None) -> bool:
    """审核者：能批准或拒绝 Team 申请的账户；其余申请人只看自己提交的记录。"""
    authorization_service = _service(context, "authorization_service")
    if authorization_service is None:
        return False
    return any(
        authorization_service.allows(actor, permission, now=now)
        for permission in ("system.team_applications.approve", "system.team_applications.reject")
    )


def _consume_application_confirmation(
        context: ModuleContext,
        data: dict[str, Any],
        *,
        application_id: str,
        operation: str,
        now: int | None,
) -> None:
    try:
        _service(context, "confirmations").consume(
            str(data.get("confirmation_token", "")).strip(),
            action=f"team.application.{operation}.confirm",
            target=application_id,
            now=now,
        )
    except ValueError as exc:
        raise ApiError(400, "confirmation_required", str(exc)) from exc
