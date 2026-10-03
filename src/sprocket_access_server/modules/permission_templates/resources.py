from __future__ import annotations

from typing import Any

from ..resource_handlers import (
    ok,
    refresh_permissions,
    require_field,
    resource_grant_node,
    timestamp,
    with_read_prerequisites,
)
from ...core.contracts import ModuleContext
from ...domain.errors import ApiError
from ...domain.resources import TEMPLATE_TEAM_ID
from ...infrastructure.events import ResourceChanged
from ...infrastructure.permissions import PermissionCatalog
from ...infrastructure.security.request_context import session_user


def _service(context: ModuleContext, name: str) -> Any:
    return context.service(name)


def _affected_template_users(context: ModuleContext, *, team_id: str, template_id: str, name: str) -> list[str]:
    template_store = _service(context, "permission_templates")
    with template_store.database.transaction() as connection:
        rows = connection.execute(
            """SELECT DISTINCT github_user_id
               FROM grants
               WHERE team_id = ?
                 AND status = 'active'
                 AND (template_id = ? OR source_id IN (?, ?))
               ORDER BY github_user_id""",
            (team_id, template_id, template_id, name),
        ).fetchall()
    return [str(row["github_user_id"]) for row in rows]


def attach(context: ModuleContext) -> None:
    platform = context.services.get("platform")
    if platform is None:
        return
    for row in platform.teams():
        register_template_resources(context, str(row["team_id"]))

    def on_team_created(event: ResourceChanged) -> None:
        if event.kind != "create" or not event.node.startswith("team."):
            return
        team_id = str(event.team_id or event.node.removeprefix("team.")).strip()
        if not team_id or "." in team_id:
            return
        with context.resources.module_scope():
            register_template_resources(context, team_id)
        refresh_permissions(context)

    context.events.subscribe(ResourceChanged, on_team_created)


def register_template_resources(context: ModuleContext, team_id: str) -> None:
    resources = context.resources
    node = f"team.{team_id}.permission_templates"
    if resources.resolve("read", node) is not None:
        return

    def read(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = session_user(_service(context, "authentication"), headers, now=now)
        _service(context, "authorization_service").require_team(
            actor, team_id, "permission_templates.read", now=now
        )
        try:
            limit = int(data.get("limit", 50))
            offset = int(data.get("offset", 0))
            values, total = _service(context, "permission_templates").search(
                active_only=False, team_id=team_id, limit=limit, offset=offset,
            )
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        catalog = _service(context, "permissions")
        return ok({
            "templates": [_template_payload(item) for item in values if item is not None],
            "catalog": sorted(catalog.permission_nodes) if catalog is not None else [],
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    def manage(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = session_user(_service(context, "authentication"), headers, now=now)
        _service(context, "authorization_service").require_team(
            actor, team_id, "permission_templates.manage", now=now
        )
        template_id = _optional_text(data.get("template_id"))
        if template_id is None:
            return _create(context, data, headers, actor=actor, team_id=team_id, now=now)
        return _update(
            context,
            data,
            headers,
            actor=actor,
            template_id=template_id,
            team_id=team_id,
            now=now,
        )

    def create(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = session_user(_service(context, "authentication"), headers, now=now)
        _service(context, "authorization_service").require_team(
            actor, team_id, "permission_templates.manage", now=now
        )
        return _create(context, data, headers, actor=actor, team_id=team_id, now=now)

    def confirm(data: dict[str, Any], headers: dict[str, str], now: int | None):
        actor = session_user(_service(context, "authentication"), headers, now=now)
        _service(context, "authorization_service").require_team(
            actor, team_id, "permission_templates.manage", now=now
        )
        template_id = require_field(data, "template_id")
        token, value = _service(context, "confirmations").issue(
            action="permission_template.disable",
            target=template_id,
            created_by=actor,
            now=now,
        )
        return ok({
            "confirmation_token": token,
            "action": value.action,
            "target": value.target,
            "expires_at": value.expires_at,
        })

    resources.register(node, description="Permission templates") \
        .add_perm("read", read) \
        .add_perm("create", create) \
        .add_perm("confirm", confirm) \
        .add_perm("manage", manage)


def _create(
        context: ModuleContext,
        data: dict[str, Any],
        headers: dict[str, str],
        *,
        actor: str,
        team_id: str,
        now: int | None,
):
    permissions = _permissions(context, data)
    if permissions is None:
        raise ApiError(400, "invalid_request", "permissions is required")
    _require_template_scope(context, actor, permissions, team_id=team_id, now=now)
    idempotency = _service(context, "idempotency")
    request_key = _require_idempotency_key(headers)
    request = {
        "name": require_field(data, "name"),
        "permissions": sorted(permissions),
        "expires_in": _optional_integer(data.get("expires_in"), "expires_in"),
        "team_id": team_id,
    }

    def operation() -> dict[str, object]:
        try:
            template = _service(context, "permission_templates").create(
                name=request["name"],
                permissions=permissions,
                expires_in=request["expires_in"],
                created_by=actor,
                team_id=team_id,
                now=now,
            )
        except ValueError as exc:
            raise _template_error(exc) from exc
        # 模板实例是权限目录里的节点：新建后目录必须重建，前端才能看到它。
        refresh_permissions(context)
        _record_audit(
            context,
            actor=actor,
            action="permission_template.create",
            template_id=template.template_id,
            team_id=team_id,
            metadata={"name": template.name, "permissions": sorted(template.permissions)},
            now=now,
        )
        return _template_payload(template)

    return _response(
        201,
        idempotency.run(
            scope=f"permission-template-create:{team_id}",
            request_key=request_key,
            request=request,
            operation=operation,
            now=now,
        ),
    )


def _update(
        context: ModuleContext,
        data: dict[str, Any],
        headers: dict[str, str],
        *,
        actor: str,
        template_id: str,
        team_id: str,
        now: int | None,
):
    store = _service(context, "permission_templates")
    current = store.get(template_id, active_only=False, team_id=team_id)
    if current is None:
        raise ApiError(404, "template_not_found", "grant template was not found")

    if str(data.get("mode", "")).strip().casefold() == "confirm":
        confirmation_action = str(
            data.get("confirmation_action", "permission_template.disable")
        ).strip()
        if confirmation_action not in {
            "permission_template.disable",
            "permission_template.update",
        }:
            raise ApiError(400, "invalid_request", "confirmation action is invalid")
        token, confirmation = _service(context, "confirmations").issue(
            action=confirmation_action,
            target=template_id,
            created_by=actor,
            now=now,
        )
        return _response(200, {
            "confirmation_token": token,
            "action": confirmation.action,
            "target": confirmation.target,
            "expires_at": confirmation.expires_at,
        })

    delete_requested = data.get("delete") is True
    status = data.get("status", "disabled" if delete_requested else current.status)
    if status not in {"active", "disabled"}:
        raise ApiError(400, "invalid_request", "grant template status is invalid")
    if delete_requested and team_id == TEMPLATE_TEAM_ID:
        # The fixed Template Team is retained; delete is an audited disable.
        status = "disabled"

    permissions = _permissions(context, data)
    if permissions is not None:
        _require_template_scope(context, actor, permissions, team_id=team_id, now=now)
    if permissions is None and any(
            key in data for key in ("name", "expires_in")
    ):
        permissions = current.permissions
    if permissions is not None and not any(
            key in data for key in ("name", "permissions", "expires_in")
    ):
        permissions = None

    if not any(key in data for key in ("name", "permissions", "expires_in")) and status == current.status:
        return _response(200, _template_payload(current))

    requested_permissions = permissions if permissions is not None else current.permissions
    requested_expires = (
        _optional_integer(data.get("expires_in"), "expires_in")
        if "expires_in" in data
        else current.expires_in
    )
    permissions_removed = sorted(current.permissions - requested_permissions)
    permissions_added = sorted(requested_permissions - current.permissions)
    expiry_shortened = (
        current.expires_in is not None
        and requested_expires is not None
        and requested_expires < current.expires_in
    )
    requires_confirmation = bool(
        status == "disabled"
        and current.status != "disabled"
        or permissions_removed
        or expiry_shortened
    )
    if data.get("preview") is True:
        affected_users = _affected_template_users(
            context,
            team_id=team_id,
            template_id=template_id,
            name=current.name,
        )
        return _response(200, {
            "diff": {
                "permissions_added": permissions_added,
                "permissions_removed": permissions_removed,
                "effect_changed": False,
                "priority_changed": False,
                "expiry_shortened": expiry_shortened,
                "expiry_changed": requested_expires != current.expires_in,
                "status_changed": status != current.status,
                "affected_users": affected_users,
                "access_impact": {
                    "adds_access": bool(permissions_added),
                    "removes_access": bool(
                        permissions_removed
                        or expiry_shortened
                        or status == "disabled" and current.status != "disabled"
                    ),
                    "changes_expiry": requested_expires != current.expires_in,
                },
                "requires_confirmation": requires_confirmation,
            },
        })

    idempotency = _service(context, "idempotency")
    request_key = _require_idempotency_key(headers)
    request = {
        "template_id": template_id,
        "name": data.get("name", current.name),
        "permissions": sorted(requested_permissions),
        "expires_in": (
            requested_expires
        ),
        "status": status,
        "delete": delete_requested,
        "team_id": team_id,
    }

    def operation() -> dict[str, object]:
        updated = current
        try:
            if requires_confirmation:
                _consume_confirmation(
                    context,
                    headers,
                    token=data.get("confirmation_token"),
                    action=(
                        "permission_template.disable"
                        if status == "disabled"
                        else "permission_template.update"
                    ),
                    target=template_id,
                    now=now,
                )
            if any(key in data for key in ("name", "permissions", "expires_in")):
                updated = store.update(
                    template_id,
                    name=str(request["name"]),
                    permissions=frozenset(request["permissions"]),
                    expires_in=request["expires_in"],
                    team_id=team_id,
                )
            if updated.status != status:
                store.set_status(template_id, status, team_id=team_id)
                updated = store.get(template_id, active_only=False, team_id=team_id)
        except ValueError as exc:
            raise _template_error(exc) from exc
        if updated is None:
            raise ApiError(404, "template_not_found", "grant template was not found")
        # 改名/停用会改变目录中的模板节点，重建目录。
        refresh_permissions(context)
        _record_audit(
            context,
            actor=actor,
            action="permission_template.disable" if delete_requested else "permission_template.update",
            template_id=template_id,
            team_id=team_id,
            metadata={"status": updated.status, "delete_requested": delete_requested},
            now=now,
        )
        return _template_payload(updated)

    return _response(
        200,
        idempotency.run(
            scope=f"permission-template-update:{team_id}",
            request_key=request_key,
            request=request,
            operation=operation,
            now=now,
        ),
    )


def _require_template_scope(
        context: ModuleContext,
        actor: str,
        permissions: frozenset[str],
        *,
        team_id: str,
        now: int | None,
) -> None:
    authorization_service = _service(context, "authorization_service")
    for permission in permissions:
        resource_grant = resource_grant_node(context, permission)
        if resource_grant is None:
            raise ApiError(400, "invalid_request", f"permission is not a registered resource: {permission}")
        authorization_service.require_grantable(
            actor,
            [resource_grant],
            team_id=team_id,
            now=now,
        )


def _permissions(context: ModuleContext, data: dict[str, Any]) -> frozenset[str] | None:
    if "permissions" not in data:
        return None
    raw = data["permissions"]
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ApiError(400, "invalid_request", "permissions must be a string array")
    try:
        values = PermissionCatalog.validate(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", str(exc)) from exc
    catalog = _service(context, "permissions")
    if catalog is not None:
        available = set(catalog.permission_nodes)
        with catalog.database.transaction() as connection:
            available.update(
                str(row["node"])
                for row in connection.execute(
                    "SELECT node FROM permission_nodes WHERE active=1"
                ).fetchall()
            )
        missing = sorted(value for value in values if value not in available)
        if missing:
            raise ApiError(400, "invalid_request", f"permission is not in the catalog: {missing[0]}")
    return frozenset(with_read_prerequisites(context, {node: "allow" for node in values}))


def _template_payload(template: Any) -> dict[str, object]:
    return {
        "template_id": template.template_id,
        "name": template.name,
        "permissions": sorted(template.permissions),
        "expires_in": template.expires_in,
        "created_by": template.created_by,
        "created_at": template.created_at,
        "status": template.status,
        "team_id": getattr(template, "team_id", None),
    }


def _record_audit(
        context: ModuleContext,
        *,
        actor: str,
        action: str,
        template_id: str,
        team_id: str,
        metadata: dict[str, object],
        now: int | None,
) -> None:
    audit = _service(context, "audit")
    if audit is not None:
        audit.record(
            actor=f"github:{actor}",
            action=action,
            target=template_id,
            metadata=metadata,
            now=timestamp(now),
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


def _template_error(exc: ValueError) -> ApiError:
    message = str(exc)
    if "not found" in message:
        return ApiError(404, "template_not_found", message)
    if "already exists" in message:
        return ApiError(409, "invalid_request", message)
    return ApiError(400, "invalid_request", message)


def _response(status: int, payload: dict[str, object]):
    from ...modules.http_common import ApiResponse

    return ApiResponse(status, payload)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _optional_integer(value: Any, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "invalid_request", f"{name} must be an integer or null") from exc


def _require_idempotency_key(headers: dict[str, str]) -> str:
    key = _header(headers, "idempotency-key")
    if not key:
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
    return key


def _header(headers: dict[str, str], name: str) -> str:
    return next(
        (str(value).strip() for key, value in headers.items()
         if str(key).casefold() == name.casefold()),
        "",
    )
