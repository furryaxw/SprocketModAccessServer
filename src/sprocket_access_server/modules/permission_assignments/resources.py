from __future__ import annotations

import time
from typing import Any

from ..resource_handlers import (
    ok,
    refresh_permissions,
    require_field,
    resource_grant_node,
    with_read_prerequisites,
)
from ...core.contracts import ModuleContext
from ...domain.errors import ApiError
from ...infrastructure.events import ResourceChanged
from ...infrastructure.permissions import PermissionCatalog
from ...infrastructure.security.request_context import session_user


def _service(context: ModuleContext, name: str) -> Any:
    return context.service(name)


def attach(context: ModuleContext) -> None:
    platform = context.services.get("platform")
    if platform is None:
        return
    for row in platform.teams():
        register_assignment_resources(context, str(row["team_id"]))

    def on_team_created(event: ResourceChanged) -> None:
        if event.kind != "create" or not event.node.startswith("team."):
            return
        team_id = str(event.team_id or event.node.removeprefix("team.")).strip()
        if not team_id or "." in team_id:
            return
        with context.resources.module_scope():
            register_assignment_resources(context, team_id)
        refresh_permissions(context)

    context.events.subscribe(ResourceChanged, on_team_created)


def register_assignment_resources(context: ModuleContext, team_id: str) -> None:
    resources = context.resources
    node = f"team.{team_id}.permission_assignments"
    if resources.resolve("read", node) is not None:
        return

    def read(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = session_user(_service(context, "authentication"), headers, now=now)
        _service(context, "authorization_service").require_team(
            actor, team_id, "permission_assignments.read", now=now
        )
        try:
            limit = _integer(data.get("limit", 50), "limit")
            offset = _integer(data.get("offset", 0), "offset")
            if not 1 <= limit <= 200 or offset < 0:
                raise ValueError("assignment pagination is invalid")
            rows, total = _service(context, "authorization").search_assignments(
                user_id=_optional_text(data.get("user_id")),
                user_id_like=_optional_text(data.get("user_id_like")),
                status=_optional_text(data.get("status")),
                expiry=_optional_text(data.get("expiry")),
                node=_optional_text(data.get("node")),
                node_like=_optional_text(data.get("node_like")),
                source=_optional_text(data.get("source")),
                limit=limit,
                offset=offset,
                team_id=team_id,
                now=now,
            )
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc

        return ok({
            "assignments": rows,
            "catalog": sorted(_service(context, "permissions").permission_nodes),
            "limit": limit,
            "offset": offset,
            "total": total,
        })

    def manage(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = session_user(_service(context, "authentication"), headers, now=now)
        _service(context, "authorization_service").require_team(
            actor, team_id, "permission_assignments.manage", now=now
        )
        mode = str(data.get("mode", "")).strip().casefold()
        if mode == "confirm":
            return ok(_issue_confirmation(context, data, actor=actor, team_id=team_id, now=now))

        store = _service(context, "authorization")
        assignment_id = _optional_text(data.get("assignment_id"))
        grant_id = _optional_text(data.get("grant_id"))
        if grant_id is None and assignment_id is not None:
            grant_id = store.grant_id_for_assignment(assignment_id, team_id=team_id)
            if grant_id is None:
                # The public identifier may already be the grant ID.
                grant_id = assignment_id
        if grant_id is None:
            return _create_assignment(
                context, data, headers, actor=actor, team_id=team_id, now=now
            )

        current = store.get_grant(grant_id, team_id=team_id)
        if current is None:
            raise ApiError(404, "grant_not_found", "grant was not found")
        _require_mutable_target(context, current.github_user_id, actor=actor, team_id=team_id)

        if mode in {"revoke", "suspend", "restore", "extend"}:
            return _run_lifecycle(
                context,
                data,
                headers,
                actor=actor,
                grant_id=grant_id,
                team_id=team_id,
                mode=mode,
                now=now,
            )

        permissions = _permissions(context, data)
        expires_at_provided = "expires_at" in data
        expires_at = _optional_integer(data.get("expires_at"), "expires_at")
        try:
            preview = store.preview_grant_update(
                grant_id,
                permissions=permissions,
                expires_at=expires_at,
                expires_at_provided=expires_at_provided,
                team_id=team_id,
            )
        except ValueError as exc:
            raise _grant_error(exc) from exc
        if permissions is not None:
            _require_assignment_scope(context, actor, permissions, team_id=team_id, now=now)
        if data.get("preview") is True:
            return ok({"preview": preview, **preview})

        idempotency = _service(context, "idempotency")
        if idempotency is None:
            raise ApiError(503, "authorization_unavailable", "idempotency service is not configured")
        request_key = _header(headers, "idempotency-key")
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
        target = _confirmation_target(team_id, grant_id)

        def operation() -> dict[str, object]:
            if preview["diff"]["permissions_removed"] or preview["diff"]["expiry_shortened"]:
                _consume_confirmation(
                    context,
                    headers,
                    token=data.get("confirmation_token"),
                    action="permission_assignment.update",
                    target=target,
                    now=now,
                )
            try:
                updated = store.update_grant(
                    grant_id,
                    permissions=permissions,
                    expires_at=expires_at,
                    expires_at_provided=expires_at_provided,
                    team_id=team_id,
                    now=now,
                )
            except ValueError as exc:
                raise _grant_error(exc) from exc
            _record_assignment_audit(
                context,
                actor=actor,
                action="permission_assignment.update",
                grant_id=grant_id,
                team_id=team_id,
                metadata={"diff": preview["diff"]},
                now=now,
            )
            return {**_grant_payload(updated), "diff": preview["diff"]}

        return ok(idempotency.run(
            scope=f"permission-assignment-update:{team_id}",
            request_key=request_key,
            request={"grant_id": grant_id, **data},
            operation=operation,
            now=now,
        ))

    resources.register(node, description="Permission assignments") \
        .add_perm("read", read) \
        .add_perm("manage", manage)


def _create_assignment(
        context: ModuleContext,
        data: dict[str, Any],
        headers: dict[str, str],
        *,
        actor: str,
        team_id: str,
        now: int | None,
):
    target_user = require_field(data, "user_id")
    _require_mutable_target(context, target_user, actor=actor, team_id=team_id)
    permissions = _permissions(context, data)
    if permissions is None:
        raise ApiError(400, "invalid_request", "permissions is required")
    _require_assignment_scope(context, actor, permissions, team_id=team_id, now=now)
    expires_at = _optional_integer(data.get("expires_at"), "expires_at")
    if expires_at is not None and expires_at <= _timestamp(now):
        raise ApiError(400, "invalid_request", "expires_at must be in the future")

    idempotency = _service(context, "idempotency")
    if idempotency is None:
        raise ApiError(503, "authorization_unavailable", "idempotency service is not configured")
    request_key = _header(headers, "idempotency-key")
    if not request_key:
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

    def operation() -> dict[str, object]:
        try:
            grant = _service(context, "authorization").create_grant(
                github_user_id=target_user,
                team_id=team_id,
                permissions=permissions,
                expires_at=expires_at,
                created_by=actor,
                now=now,
            )
        except ValueError as exc:
            raise _grant_error(exc) from exc
        _record_assignment_audit(
            context,
            actor=actor,
            action="permission_assignment.create",
            grant_id=grant.grant_id,
            team_id=team_id,
            metadata={"user_id": target_user, "permissions": sorted(permissions)},
            now=now,
        )
        return _grant_payload(grant)

    return ok(idempotency.run(
        scope=f"permission-assignment-create:{team_id}",
        request_key=request_key,
        request={
            "user_id": target_user,
            "permissions": {node: effect for node, effect in sorted(permissions.items())},
            "expires_at": expires_at,
        },
        operation=operation,
        now=now,
    ))


def _run_lifecycle(
        context: ModuleContext,
        data: dict[str, Any],
        headers: dict[str, str],
        *,
        actor: str,
        grant_id: str,
        team_id: str,
        mode: str,
        now: int | None,
):
    idempotency = _service(context, "idempotency")
    if idempotency is None:
        raise ApiError(503, "authorization_unavailable", "idempotency service is not configured")
    request_key = _header(headers, "idempotency-key")
    if not request_key:
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
    expires_at = _optional_integer(data.get("expires_at"), "expires_at") if mode == "extend" else None
    if mode == "extend" and expires_at is None:
        raise ApiError(400, "invalid_request", "expires_at is required")
    action = f"permission_assignment.{mode}"
    target = _confirmation_target(team_id, grant_id)

    def operation() -> dict[str, object]:
        if mode in {"revoke", "suspend"}:
            _consume_confirmation(
                context,
                headers,
                token=data.get("confirmation_token"),
                action=action,
                target=target,
                now=now,
            )
        store = _service(context, "authorization")
        try:
            if mode == "revoke":
                store.revoke_grant(grant_id, now=now, team_id=team_id)
                status = "revoked"
            elif mode == "suspend":
                store.set_grant_status(grant_id, "suspended", now=now, team_id=team_id)
                status = "suspended"
            elif mode == "restore":
                store.set_grant_status(grant_id, "active", now=now, team_id=team_id)
                status = "active"
            else:
                store.extend_grant(grant_id, expires_at, now=now, team_id=team_id)
                status = "active"
        except ValueError as exc:
            raise _grant_error(exc) from exc
        _record_assignment_audit(
            context,
            actor=actor,
            action=action,
            grant_id=grant_id,
            team_id=team_id,
            metadata={"status": status, "expires_at": expires_at},
            now=now,
        )
        current = store.get_grant(grant_id, team_id=team_id)
        return {
            "grant_id": grant_id,
            "status": status,
            "expires_at": current.expires_at if current is not None else expires_at,
        }

    return ok(idempotency.run(
        scope=f"permission-assignment-{mode}:{team_id}",
        request_key=request_key,
        request={"grant_id": grant_id, "mode": mode, "expires_at": expires_at},
        operation=operation,
        now=now,
    ))


def _issue_confirmation(
        context: ModuleContext,
        data: dict[str, Any],
        *,
        actor: str,
        team_id: str,
        now: int | None,
) -> dict[str, object]:
    assignment_id = require_field(data, "assignment_id")
    store = _service(context, "authorization")
    grant_id = store.grant_id_for_assignment(assignment_id, team_id=team_id) or assignment_id
    mode = str(data.get("operation", "update")).strip().casefold()
    action = "permission_assignment.suspend" if mode == "suspend" else (
        "permission_assignment.revoke" if mode == "revoke" else "permission_assignment.update"
    )
    grant = store.get_grant(grant_id, team_id=team_id)
    if grant is None:
        raise ApiError(404, "grant_not_found", "grant was not found")
    _require_mutable_target(context, grant.github_user_id, actor=actor, team_id=team_id)
    token, confirmation = _service(context, "confirmations").issue(
        action=action,
        target=_confirmation_target(team_id, grant_id),
        created_by=actor,
        now=now,
    )
    return {
        "confirmation_token": token,
        "action": confirmation.action,
        "target": confirmation.target,
        "expires_at": confirmation.expires_at,
    }


def _require_assignment_scope(
        context: ModuleContext,
        actor: str,
        permissions: frozenset[str],
        *,
        team_id: str,
        now: int | None,
) -> None:
    authorization_service = _service(context, "authorization_service")
    for permission in permissions:
        grant_node = resource_grant_node(context, permission)
        if grant_node is None:
            raise ApiError(400, "invalid_request", f"permission is not a registered resource: {permission}")
        authorization_service.require_grantable(
            actor, [grant_node], team_id=team_id, now=now
        )


def _require_mutable_target(context: ModuleContext, target_user: str, *, actor: str, team_id: str) -> None:
    platform = _service(context, "platform")
    if platform.user(target_user) is None:
        raise ApiError(404, "not_found", "target user was not found")


def _permissions(context: ModuleContext, data: dict[str, Any]) -> dict[str, str] | None:
    """读取提交的权限映射 `{节点: effect}`。

    effect 只允许 `allow` / `deny`；节点必须是目录（或库内仍保留的）节点；动作节点会
    由后端补同资源的 `read` 前置。
    """
    if "permissions" not in data:
        return None
    raw = data["permissions"]
    if not isinstance(raw, dict) or not all(
            isinstance(node, str) and isinstance(effect, str) for node, effect in raw.items()):
        raise ApiError(400, "invalid_request", "permissions must be an object of node/effect pairs")
    try:
        values = PermissionCatalog.validate(list(raw))
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    catalog = _service(context, "permissions")
    if catalog is not None:
        with catalog.database.transaction() as connection:
            stored = {
                str(row["node"])
                for row in connection.execute(
                    "SELECT node FROM permission_nodes WHERE active=1"
                ).fetchall()
            }
        available = set(catalog.permission_nodes) | stored
        missing = sorted(value for value in values if value not in available)
        if missing:
            raise ApiError(400, "invalid_request", f"permission is not in the catalog: {missing[0]}")
    effects = {str(node): str(effect).strip().casefold() for node, effect in raw.items()}
    invalid = sorted(effect for effect in effects.values() if effect not in {"allow", "deny"})
    if invalid:
        raise ApiError(400, "invalid_request", "permission effect must be allow or deny")
    return with_read_prerequisites(context, effects)


def _assignment_payload(grant: Any, assignment: Any) -> dict[str, object]:
    return {
        "assignment_id": assignment.assignment_id,
        "grant_id": grant.grant_id,
        "github_user_id": grant.github_user_id,
        "node": assignment.node,
        "effect": assignment.effect,
        "priority": assignment.priority,
        "grant_effect": assignment.grant,
        "source_type": assignment.source_type,
        "source_id": assignment.source_id,
        "starts_at": assignment.starts_at,
        "expires_at": assignment.expires_at or grant.expires_at,
        "revoked_at": assignment.revoked_at or grant.revoked_at,
        "status": grant.status.value,
    }


def _grant_payload(grant: Any) -> dict[str, object]:
    return {
        "grant_id": grant.grant_id,
        "github_user_id": grant.github_user_id,
        "permissions": sorted(grant.permissions),
        "status": grant.status.value,
        "expires_at": grant.expires_at,
        "source_type": grant.source_type,
        "source_id": grant.source_id,
    }


def _record_assignment_audit(
        context: ModuleContext,
        *,
        actor: str,
        action: str,
        grant_id: str,
        team_id: str,
        metadata: dict[str, object],
        now: int | None,
) -> None:
    audit = _service(context, "audit")
    if audit is not None:
        audit.record(
            actor=f"github:{actor}",
            action=action,
            target=grant_id,
            metadata=metadata,
            now=_timestamp(now),
            team_id=team_id,
        )


def _consume_confirmation(
        context: ModuleContext,
        headers: dict[str, str],
        *,
        token: Any = None,
        action: str,
        target: str,
        now: int | None,
) -> None:
    try:
        _service(context, "confirmations").consume(
            str(token or _header(headers, "x-confirmation-token")),
            action=action,
            target=target,
            now=now,
        )
    except ValueError as exc:
        raise ApiError(400, "confirmation_required", str(exc)) from exc


def _grant_error(exc: ValueError) -> ApiError:
    message = str(exc)
    if "not found" in message:
        return ApiError(404, "grant_not_found", message)
    if "confirmation" in message:
        return ApiError(400, "confirmation_required", message)
    return ApiError(409, "invalid_request", message)


def _confirmation_target(team_id: str, grant_id: str) -> str:
    return f"{team_id}:{grant_id}"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _optional_integer(value: Any, name: str) -> int | None:
    if value is None:
        return None
    return _integer(value, name)


def _integer(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _timestamp(now: int | None) -> int:
    return int(time.time()) if now is None or now <= 0 else now


def _header(headers: dict[str, str], name: str) -> str:
    return next(
        (str(value).strip() for key, value in headers.items()
         if str(key).casefold() == name.casefold()),
        "",
    )
