from __future__ import annotations

import logging
import time
from typing import Any

from ..resource_handlers import ok, refresh_permissions, require_field, with_read_prerequisites
from ...core.contracts import ModuleContext
from ...domain.errors import ApiError
from ...domain.resources import SYSTEM_TEAM_ID
from ...infrastructure.events import ResourceChanged
from ...infrastructure.permissions import PermissionCatalog
from ...infrastructure.security.request_context import bearer, platform_user

logger = logging.getLogger(__name__)


def _service(context: ModuleContext, name: str) -> Any:
    return context.service(name)


def attach(context: ModuleContext) -> None:
    resources = context.resources
    platform = context.services.get("platform")

    def redeem(data: dict[str, Any], headers: dict[str, str], now: int | None):
        authentication = _service(context, "authentication")
        authorization = _service(context, "authorization")
        key_issuer = _service(context, "key_issuer")
        idempotency = _service(context, "idempotency")
        token = bearer(headers)
        user_id = authentication.authenticate_session(token, now=now)
        key = str(data.get("key", "")).strip()
        if not key:
            raise ApiError(400, "invalid_request", "key is required")
        request_key = next((str(value) for key, value in headers.items() if str(key).casefold() == "idempotency-key"),
                           "").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
        key_hash = key_issuer.hash_key(key)
        # Key 决定 Team：客户端不需要（也不应该）预先声明 team_id。显式头只作为旧客户端提示，
        # 与 Key 不符时以 Key 为准。
        team_id = key_issuer.team_for_key(key_hash)
        declared_team = next(
            (str(value).strip() for key_name, value in headers.items() if str(key_name).casefold() == "x-team-id"),
            "",
        )
        if team_id is None:
            if not declared_team:
                raise ApiError(409, "key_unavailable", "activation key is unavailable")
            team_id = declared_team
        elif declared_team and declared_team != team_id:
            logger.warning(
                "key redeem team mismatch declared=%s key_team=%s",
                declared_team, team_id,
            )

        def operation() -> dict[str, object]:
            try:
                grant = authorization.redeem_key(
                    key_hash=key_hash,
                    github_user_id=user_id,
                    now=now,
                    team_id=team_id,
                )
            except ValueError as exc:
                raise ApiError(409, "key_unavailable", str(exc)) from exc
            permissions = authorization.permissions(user_id, now=now, team_id=team_id)

            def grant_payload(item) -> dict[str, object]:
                return {
                    "grant_id": item.grant_id,
                    "github_user_id": item.github_user_id,
                    "permissions": sorted(item.permissions),
                    "expires_at": item.expires_at,
                    "state": item.status.value,
                }

            return {
                "github_user_id": user_id,
                "team_id": team_id,
                "permissions": sorted(permissions),
                "grant": grant_payload(grant),
                "grants": [grant_payload(item) for item in authorization.grants(user_id, team_id=team_id)],
            }

        if idempotency is None:
            return ok(operation())
        return ok(idempotency.run(
            scope="v1:keys:redeem",
            request_key=request_key,
            request={"key_hash": key_hash, "github_user_id": user_id},
            operation=operation,
            now=now,
        ))

    def register_key_resources(team_id: str) -> None:
        node = f"team.{team_id}.keys"
        if resources.resolve("read", node) is not None:
            return

        def read(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            key_issuer = _service(context, "key_issuer")
            status = data.get("status")
            prefix = str(data.get("prefix", ""))
            batch_id = data.get("batch_id")
            delivered = data.get("delivered") if isinstance(data.get("delivered"), bool) else None
            limit = int(data.get("limit", 50))
            offset = int(data.get("offset", 0))
            # System 工作区是平台视图：列出所有 Team 的 Key（与 team.system.packages 的
            # 目录口径一致），每条带 team_id 供表格显示归属。
            keys, counts, total = key_issuer.search(
                status=status if isinstance(status, str) else None,
                prefix=prefix,
                batch_id=batch_id if isinstance(batch_id, str) else None,
                delivered=delivered,
                limit=limit,
                offset=offset,
                team_id=None if team_id == SYSTEM_TEAM_ID else team_id,
            )
            return ok({
                "keys": [item.__dict__ for item in keys],
                "catalog": sorted(_service(context, "permissions").permission_nodes),
                "counts": counts,
                "total": total,
                "limit": limit,
                "offset": offset,
            })

        def distribute(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            actor = _require_team_key_manage(context, headers, team_id, now=now)
            key_issuer = _service(context, "key_issuer")
            idempotency = _service(context, "idempotency")
            permissions = data.get("permissions", [])
            if not isinstance(permissions, list):
                raise ApiError(400, "invalid_request", "permissions must be a list")
            validated = frozenset(with_read_prerequisites(
                context,
                {node: "allow" for node in PermissionCatalog.validate(permissions)},
            ))
            authorization = _service(context, "authorization_service")
            authorization.require_grantable(
                actor,
                sorted(validated),
                team_id=team_id,
                now=now,
            )
            quantity = int(data.get("quantity", 1))
            expires_at = data.get("expires_at")
            if expires_at is not None:
                expires_at = int(expires_at)
            request_key = next(
                (
                    str(value).strip()
                    for key, value in headers.items()
                    if str(key).casefold() == "idempotency-key"
                ),
                "",
            )
            if idempotency is None or not request_key:
                raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

            def operation() -> dict[str, object]:
                issued = key_issuer.create_batch(
                    quantity=quantity,
                    permissions=validated,
                    expires_at=expires_at,
                    actor=actor,
                    team_id=team_id,
                    now=now,
                )
                return {"keys": [item.__dict__ for item in issued]}

            return ok(idempotency.run(
                scope=f"key-distribute:{team_id}",
                request_key=request_key,
                request={
                    "quantity": quantity,
                    "permissions": sorted(validated),
                    "expires_at": expires_at,
                },
                operation=operation,
                now=now,
            ))

        def batch_stats(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            key_issuer = _service(context, "key_issuer")
            batch_id = require_field(data, "batch_id")
            return ok(key_issuer.batch_stats(batch_id, team_id=team_id))

        def take(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            """从批次里取走若干枚码并记下发给谁：并发取用不会重复发同一枚。"""
            actor = _require_team_key_action(context, headers, team_id, "keys.take", now=now)
            key_issuer = _service(context, "key_issuer")
            batch_id = require_field(data, "batch_id")
            recipient = require_field(data, "recipient")
            count = int(data.get("count", 1))
            try:
                keys = key_issuer.take_batch(
                    batch_id=batch_id, count=count, recipient=recipient, actor=actor,
                    team_id=None if team_id == SYSTEM_TEAM_ID else team_id, now=now,
                )
            except (TypeError, ValueError) as exc:
                raise ApiError(409, "key_unavailable", str(exc)) from exc
            audit = _service(context, "audit")
            if audit is not None:
                audit.record(actor=actor, action="keys.take", target=batch_id,
                             metadata={"count": len(keys), "recipient": recipient},
                             now=int(time.time()) if now is None or now <= 0 else now)
            return ok({"keys": [item.__dict__ for item in keys], "count": len(keys)})

        def release(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            """撤销发放记录，让这枚码回到"未发放"。"""
            actor = _require_team_key_action(context, headers, team_id, "keys.release", now=now)
            key_issuer = _service(context, "key_issuer")
            key_id = require_field(data, "key_id")
            try:
                key_issuer.release_key(
                    key_id, actor=actor, team_id=None if team_id == SYSTEM_TEAM_ID else team_id, now=now,
                )
            except (TypeError, ValueError) as exc:
                raise ApiError(409, "key_unavailable", str(exc)) from exc
            audit = _service(context, "audit")
            if audit is not None:
                audit.record(actor=actor, action="keys.release", target=key_id, metadata={},
                             now=int(time.time()) if now is None or now <= 0 else now)
            return ok({"key_id": key_id, "delivered_to": None})

        def confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            actor = _require_team_key_manage(context, headers, team_id, now=now)
            confirmations = _service(context, "confirmations")
            key_id = require_field(data, "key_id")
            token, confirmation_obj = confirmations.issue(action="key.confirm", target=key_id, created_by=actor,
                                                          now=now)
            return ok({"confirmation_token": token, "confirmation": confirmation_obj.__dict__})

        def manage(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            actor = _require_team_key_manage(context, headers, team_id, now=now)
            key_issuer = _service(context, "key_issuer")
            key_id = require_field(data, "key_id")
            note = str(data.get("note", "")) if "note" in data else None
            permissions = PermissionCatalog.validate(data["permissions"]) if "permissions" in data else None
            expires_at = (
                int(data["expires_at"])
                if "expires_at" in data and data["expires_at"] is not None
                else None
            )
            expires_at_provided = "expires_at" in data
            status = str(data.get("status")) if "status" in data and data["status"] is not None else None
            preview = key_issuer.preview_update(
                key_id,
                note=note,
                permissions=permissions,
                expires_at=expires_at,
                expires_at_provided=expires_at_provided,
                status=status,
                team_id=team_id,
            )
            if data.get("preview") is True:
                return ok(preview)
            idempotency = _service(context, "idempotency")
            request_key = next(
                (
                    str(value).strip()
                    for header, value in headers.items()
                    if str(header).casefold() == "idempotency-key"
                ),
                "",
            )
            if idempotency is None or not request_key:
                raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

            def operation() -> dict[str, object]:
                if preview["requires_confirmation"]:
                    token = data.get("confirmation_token")
                    try:
                        _service(context, "confirmations").consume(
                            str(token or ""),
                            action="key.confirm",
                            target=key_id,
                            now=now,
                        )
                    except ValueError as exc:
                        raise ApiError(400, "confirmation_required", str(exc)) from exc
                return key_issuer.update(
                    key_id,
                    actor=actor,
                    note=note,
                    permissions=permissions,
                    expires_at=expires_at,
                    expires_at_provided=expires_at_provided,
                    status=status,
                    team_id=team_id,
                    now=now,
                )

            return ok(idempotency.run(
                scope=f"key-update:{team_id}",
                request_key=request_key,
                request={"key_id": key_id, "note": note, "status": status},
                operation=operation,
                now=now,
            ))

        resources.register(node, description="Team Keys") \
            .add_perm("read", read) \
            .add_perm("manage", manage) \
            .add_perm("distribute", distribute) \
            .add_perm("take", take) \
            .add_perm("release", release) \
            .add_perm("read_batch", batch_stats) \
            .add_perm("confirm", confirmation)

    for item in platform.teams() if platform is not None else []:
        register_key_resources(str(item["team_id"]))

    def on_team_created(event: ResourceChanged) -> None:
        if event.kind != "create" or not event.node.startswith("team."):
            return
        team_id = str(event.team_id or event.node.removeprefix("team.")).strip()
        if not team_id or "." in team_id:
            return
        with resources.module_scope():
            register_key_resources(team_id)
        refresh_permissions(context)

    context.events.subscribe(ResourceChanged, on_team_created)

    # 客户端兑换：凭 session + 显式 Team 上下文 + Key 本身完成，不要求管理端权限节点。
    resources.register("system.keys.redeem", description="Key redemption") \
        .add_public("distribute", redeem)


def _require_team_key_action(context: ModuleContext, headers: dict[str, str], team_id: str, action: str, *,
                             now: int | None) -> str:
    """平台管理员或该 Team 的对应节点持有者。动作名即节点后缀，避免动作之间互相绑定。"""
    actor, _ = platform_user(_service(context, "authentication"), _service(context, "platform"), headers, now=now)
    authorization_service = _service(context, "authorization_service")
    if authorization_service is None:
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    authorization_service.require_system_or_team(
        actor,
        "system.teams.manage",
        team_id,
        action,
        now=now,
    )
    return actor


def _require_team_key_manage(context: ModuleContext, headers: dict[str, str], team_id: str, *, now: int | None) -> str:
    return _require_team_key_action(context, headers, team_id, "keys.distribute", now=now)
