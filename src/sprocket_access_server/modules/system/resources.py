from __future__ import annotations

import base64
import time
from typing import Any

from ..resource_handlers import ok
from ...core.contracts import ModuleContext
from ...domain.errors import ApiError
from ...domain.resources import SYSTEM_TEAM_ID
from ...infrastructure.security.signing import canonical_json
from ...infrastructure.security.request_context import (
    bearer,
    require_context_team,
    session_user,
    team_context,
)
from ...infrastructure.events import ResourceChanged


def _service(context: ModuleContext, name: str) -> Any:
    return context.service(name)


def _overview_payload(
        context: ModuleContext,
        *,
        team_id: str | None,
        actor: str,
        now: int | None,
        audit_actor: str | None = None,
        audit_action: str | None = None,
        audit_target: str | None = None,
        audit_limit: int = 8,
        audit_offset: int = 0,
) -> dict[str, object]:
    key_issuer = _service(context, "key_issuer")
    audit = _service(context, "audit")
    authorization_service = _service(context, "authorization_service")
    timestamp = int(time.time()) if now is None or now <= 0 else now
    if key_issuer is None:
        raise ApiError(503, "admin_unavailable", "overview data is not configured")
    database = key_issuer.database
    team_filter = "(? IS NULL OR team_id = ?)"
    with database.transaction() as connection:
        grants = connection.execute(
            f"""SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='active' AND expires_at IS NOT NULL AND expires_at<=?
                                THEN 1 ELSE 0 END) AS expired
                FROM grants
                WHERE {team_filter}""",
            (timestamp, team_id, team_id),
        ).fetchone()
        packages = connection.execute(
            f"SELECT COUNT(*) AS total FROM package_versions WHERE {team_filter}",
            (team_id, team_id),
        ).fetchone()
        uploads = connection.execute(
            f"""SELECT SUM(CASE WHEN status='pending' AND expires_at>? THEN 1 ELSE 0 END) AS pending,
                       SUM(CASE WHEN status='pending' AND expires_at<=? THEN 1 ELSE 0 END) AS timed_out
                FROM upload_drafts
                WHERE {team_filter}""",
            (timestamp, timestamp, team_id, team_id),
        ).fetchone()
        if team_id is None:
            users_count = connection.execute(
                "SELECT COUNT(*) FROM users WHERE status='active'"
            ).fetchone()[0]
            teams_count = connection.execute(
                "SELECT COUNT(*) FROM teams WHERE status='active'"
            ).fetchone()[0]
        else:
            users_count = connection.execute(
                "SELECT COUNT(DISTINCT github_user_id) FROM grants "
                "WHERE team_id=? AND status='active'",
                (team_id,),
            ).fetchone()[0]
            teams_count = None

        pending_applications = 0
        if team_id is None and authorization_service is not None and authorization_service.allows(
                actor, "system.team_applications.read", now=now
        ):
            pending_applications = connection.execute(
                "SELECT COUNT(*) FROM team_applications WHERE status='pending'"
            ).fetchone()[0]

    _, key_counts, key_total = key_issuer.search(limit=1, team_id=team_id)
    payload: dict[str, object] = {
        "workspace_kind": "system" if team_id is None else "team",
        "team_id": team_id,
        "keys": {"total": key_total, "by_status": key_counts},
        "grants": {
            "total": int(grants["total"] or 0),
            "expired": int(grants["expired"] or 0),
        },
        "users": int(users_count or 0),
        "packages": int(packages["total"] or 0),
        "uploads": {
            "pending": int(uploads["pending"] or 0),
            "timed_out": int(uploads["timed_out"] or 0),
        },
        "audit": audit.search(
            query=audit_actor,
            limit=audit_limit,
            offset=audit_offset,
            team_id=team_id,
        ) if audit else [],
        "audit_total": audit.count(
            query=audit_actor,
            team_id=team_id,
        ) if audit else 0,
    }
    if team_id is None:
        payload.update({
            "teams": int(teams_count or 0),
            "applications": (
                {"pending": int(pending_applications or 0)}
                if authorization_service is not None and authorization_service.allows(
                    actor, "system.team_applications.read", now=now
                )
                else None
            ),
            "health": (
                {
                    "service": "ok",
                    "database": "ok" if database.integrity_check() else "error",
                    "storage": "configured" if _service(context, "publisher") is not None else "unavailable",
                }
                if authorization_service is not None and authorization_service.allows(
                    actor, "system.operations.status.read", now=now
                )
                else None
            ),
        })
    return payload


def attach(context: ModuleContext) -> None:
    resources = context.resources

    def server_info(_data: dict[str, Any], _headers: dict[str, str], _now: int | None):
        payload = _service(context, "server_info").payload()
        authentication = context.services.get("authentication")
        if authentication is not None and hasattr(authentication, "github_auth_methods"):
            identity_policy = dict(payload.get("identity_policy", {}))
            identity_policy["auth_methods"] = authentication.github_auth_methods()
            payload = {**payload, "identity_policy": identity_policy}
        return ok(payload)

    def key_status(_data: dict[str, Any], _headers: dict[str, str], now: int | None):
        signing_key = _service(context, "signing_key")
        signing_key_id = _service(context, "signing_key_id")
        if signing_key is None or not signing_key_id:
            raise ApiError(503, "signing_unavailable", "signing identity is not configured")
        issued_at = int(time.time()) if now is None or now <= 0 else now
        status = {
            "schema_version": 1,
            "server_id": _service(context, "server_info").server_id,
            "active_key_id": signing_key_id,
            "revoked_key_ids": [],
            "issued_at": issued_at,
            "expires_at": issued_at + 24 * 60 * 60,
        }
        signed = signing_key.sign(canonical_json(status))
        signature = {
            "format": "detached-canonical-json",
            "algorithm": "ed25519",
            "encoding": "base64url",
            "key_id": signing_key_id,
            "signature": base64.urlsafe_b64encode(signed).decode("ascii").rstrip("="),
        }
        return ok({"status": status, "signature": signature})

    def github_exchange(data: dict[str, Any], _headers: dict[str, str], now: int | None):
        token = data.get("access_token")
        if not isinstance(token, str):
            raise ApiError(400, "invalid_request", "access_token is required")
        authentication = _service(context, "authentication")
        return ok(authentication.exchange_github_token(token, now=now).payload())

    def github_device_start(_data: dict[str, Any], _headers: dict[str, str], now: int | None):
        return ok(_service(context, "authentication").start_github_device_flow(now=now))

    def github_device_poll(data: dict[str, Any], _headers: dict[str, str], now: int | None):
        flow_id = data.get("flow_id")
        if not isinstance(flow_id, str) or not flow_id:
            raise ApiError(400, "invalid_request", "flow_id is required")
        result = _service(context, "authentication").poll_github_device_flow(flow_id, now=now)
        return ok(result.payload() if hasattr(result, "payload") else result)

    def github_web_start(_data: dict[str, Any], _headers: dict[str, str], now: int | None):
        return ok(_service(context, "authentication").start_github_web_flow(now=now))

    def service_exchange(data: dict[str, Any], _headers: dict[str, str], now: int | None):
        """服务号密钥换会话：公开动作，凭据本身即授权。"""
        service_id = data.get("service_id")
        secret = data.get("secret")
        if not isinstance(service_id, str) or not isinstance(secret, str):
            raise ApiError(400, "invalid_request", "service_id and secret are required")
        result = _service(context, "authentication").exchange_service_credential(
            service_id, secret, now=now,
        )
        # 成功兑换落审计：这是外部服务取得会话的唯一入口。失败只记日志，避免猜测尝试刷审计表。
        audit = context.services.get("audit")
        if audit is not None:
            audit.record(actor=f"service:{service_id}", action="service.exchange", target="session", metadata={},
                         now=int(time.time()) if now is None or now <= 0 else now)
        return ok(result.payload())

    def auth_me(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        authentication = _service(context, "authentication")
        authorization_service = _service(context, "authorization_service")
        platform = _service(context, "platform")
        user_id = session_user(authentication, headers, now=now)
        if platform is None:
            raise ApiError(503, "admin_unavailable", "platform service is not configured")
        user = platform.user(user_id) or {"github_user_id": user_id}
        team_id = team_context(authorization_service, headers, user_id, required=False)
        if authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        authorization_context = authorization_service.context(user_id, team_id=team_id, now=now)
        membership = next(
            (
                item
                for item in authorization_service.memberships(user_id)
                if str(item.get("team_id", "")) == team_id
            ),
            None,
        ) if team_id else None
        permissions = (
            sorted(
                permission
                for permission in authorization_context.effective_permissions
                if permission.startswith(f"team.{team_id}.")
            )
            if team_id
            else sorted(authorization_context.effective_permissions)
        )
        return ok({
            **user,
            "current_team": membership,
            "permissions": permissions,
            "effective_permissions": sorted(authorization_context.effective_permissions),
            "grantable_permissions": sorted(authorization_context.grantable_permissions),
        })

    def auth_me_teams(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        authentication = _service(context, "authentication")
        authorization_service = _service(context, "authorization_service")
        platform = _service(context, "platform")
        user_id = session_user(authentication, headers, now=now)
        if platform is None:
            raise ApiError(503, "admin_unavailable", "platform service is not configured")
        if authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        teams = authorization_service.memberships(user_id)
        teams = [
            {
                **team,
                "workspace_kind": (
                    "system" if str(team.get("team_id", "")) == SYSTEM_TEAM_ID else "team"
                ),
            }
            for team in teams
        ]
        return ok({"teams": teams})

    def auth_me_authorization(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        authentication = _service(context, "authentication")
        authorization_service = _service(context, "authorization_service")
        user_id = session_user(authentication, headers, now=now)
        if authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        team_id = team_context(authorization_service, headers, user_id, required=False)
        value = authorization_service.context(user_id, team_id=team_id, now=now)
        return ok({
            "user_id": value.user_id,
            "system_permissions": sorted(value.system_permissions),
            "effective_permissions": sorted(value.effective_permissions),
            "grantable_permissions": sorted(value.grantable_permissions),
            "permission_assignments": [
                {
                    "assignment_id": item.assignment_id,
                    "node": item.node,
                    "effect": item.effect,
                    "priority": item.priority,
                    "grant": item.grant,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "starts_at": item.starts_at,
                    "expires_at": item.expires_at,
                    "revoked_at": item.revoked_at,
                }
                for item in value.permission_assignments
            ],
            "team": ({
                         "team_id": value.team_id,
                         "permissions": sorted(value.team_permissions),
                         "content_permissions": sorted(value.content_permissions),
                         "tester_scopes": sorted(value.tester_scopes),
                     } if value.team_id else None),
        })

    def select_team_context(data: dict[str, Any], headers: dict[str, str], now: int | None):
        authentication = _service(context, "authentication")
        authorization_service = _service(context, "authorization_service")
        platform = _service(context, "platform")
        user_id = session_user(authentication, headers, now=now)
        team_id = str(data.get("team_id", "")).strip()
        if not team_id or platform is None or authorization_service is None:
            raise ApiError(400, "invalid_request", "team_id is required")
        membership = next(
            (
                item
                for item in authorization_service.memberships(user_id)
                if str(item.get("team_id", "")) == team_id
            ),
            None,
        )
        if membership is None:
            raise ApiError(403, "permission_denied", "user is not a member of this Team")
        return ok({"team": membership})

    def revoke_session(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        authentication = _service(context, "authentication")
        audit = _service(context, "audit")
        token = bearer(headers)
        user_id = authentication.authenticate_session(token, now=now)
        authentication.revoke_session(token, now=now)
        if audit is not None:
            audit.record(actor=f"github:{user_id}", action="session.revoke", target="session", metadata={},
                         now=int(time.time()) if now is None or now <= 0 else now)
        return ok({"ok": True})

    def platform_config(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        authentication = _service(context, "authentication")
        platform = _service(context, "platform")
        settings = _service(context, "settings") if "settings" in context.services else None
        user_id = session_user(authentication, headers, now=now)
        user = platform.user(user_id) if platform else None
        return ok({
            "mode": platform.mode if platform else "simple",
            "auto_register_users": bool(getattr(settings, "auto_register_users", True)),
            "first_login_permission_template": str(getattr(settings, "first_login_permission_template", "template.owner")),
            "new_user_permission_template": str(getattr(settings, "new_user_permission_template", "template.user")),
        })

    def overview(data: dict[str, Any], headers: dict[str, str], now: int | None):
        require_context_team(headers, SYSTEM_TEAM_ID)
        actor = session_user(_service(context, "authentication"), headers, now=now)
        authorization_service = _service(context, "authorization_service")
        if authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        authorization_service.require_system(actor, "system.overview.read", now=now)
        try:
            audit_limit = int(data.get("audit_limit", 8))
            audit_offset = int(data.get("audit_offset", 0))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", "audit pagination is invalid") from exc
        if not 1 <= audit_limit <= 100 or audit_offset < 0:
            raise ApiError(400, "invalid_request", "audit pagination is invalid")
        return ok(_overview_payload(
            context,
            team_id=None,
            actor=actor,
            now=now,
            audit_actor=str(data.get("audit_actor", "")).strip() or None,
            audit_action=str(data.get("audit_action", "")).strip() or None,
            audit_target=str(data.get("audit_target", "")).strip() or None,
            audit_limit=audit_limit,
            audit_offset=audit_offset,
        ))

    def register_team_overview(team_id: str) -> None:
        node = f"team.{team_id}.overview"
        if resources.resolve("read", node) is not None:
            return

        def team_overview(data: dict[str, Any], headers: dict[str, str], now: int | None,
                          *, team_id: str = team_id):
            require_context_team(headers, team_id)
            actor = session_user(_service(context, "authentication"), headers, now=now)
            authorization_service = _service(context, "authorization_service")
            if authorization_service is None:
                raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
            authorization_service.require_team(actor, team_id, "overview.read", now=now)
            try:
                audit_limit = int(data.get("audit_limit", 8))
                audit_offset = int(data.get("audit_offset", 0))
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "invalid_request", "audit pagination is invalid") from exc
            if not 1 <= audit_limit <= 100 or audit_offset < 0:
                raise ApiError(400, "invalid_request", "audit pagination is invalid")
            return ok(_overview_payload(
                context,
                team_id=team_id,
                actor=actor,
                now=now,
                audit_actor=str(data.get("audit_actor", "")).strip() or None,
                audit_action=str(data.get("audit_action", "")).strip() or None,
                audit_target=str(data.get("audit_target", "")).strip() or None,
                audit_limit=audit_limit,
                audit_offset=audit_offset,
            ))

        resources.register(node, description="Team overview").add_perm("read", team_overview)

    platform = context.services.get("platform")
    if platform is not None:
        for row in platform.teams():
            team_id = str(row["team_id"])
            if team_id != SYSTEM_TEAM_ID:
                register_team_overview(team_id)

    def on_team_created(event: ResourceChanged) -> None:
        if event.kind != "create" or not event.node.startswith("team."):
            return
        team_id = str(event.team_id or event.node.removeprefix("team.")).strip()
        if not team_id or "." in team_id or team_id == SYSTEM_TEAM_ID:
            return
        with resources.module_scope():
            register_team_overview(team_id)
        _service(context, "permissions").permission_nodes = context.resources.permission_nodes()

    context.events.subscribe(ResourceChanged, on_team_created)

    def operations_status(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        authentication = _service(context, "authentication")
        authorization_service = _service(context, "authorization_service")
        key_issuer = _service(context, "key_issuer")
        publisher = _service(context, "publisher")
        user_id = session_user(authentication, headers, now=now)
        if authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        require_context_team(headers, SYSTEM_TEAM_ID)
        authorization_service.require_system(user_id, "system.operations.status.read", now=now)
        if key_issuer is None:
            raise ApiError(503, "admin_unavailable", "operations data is not configured")
        database = key_issuer.database
        return ok({
            "items": [
                {"component": "service", "status": "ok", "detail": "HTTP API is responding"},
                {"component": "database", "status": "ok" if database.integrity_check() else "error",
                 "detail": f"schema {database.schema_version}"},
                {"component": "storage", "status": "configured" if publisher is not None else "unavailable",
                 "detail": "package publisher"},
                {"component": "schema", "status": "ok", "detail": str(database.schema_version)},
            ]
        })

    def list_permissions(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        authentication = _service(context, "authentication")
        authorization_service = _service(context, "authorization_service")
        permissions = _service(context, "permissions")
        package_store = _service(context, "packages")
        template_store = _service(context, "permission_templates")
        user_id = session_user(authentication, headers, now=now)
        if authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        authorization_service.require_team(user_id, SYSTEM_TEAM_ID, "users.read", now=now)
        team_id = team_context(authorization_service, headers, user_id, required=False)
        templates = template_store.list(team_id=team_id) if template_store is not None else ()
        return ok({
            "permissions": [item.__dict__ for item in permissions.list(team_id=team_id, package_store=package_store,
                                                                       template_store=template_store)],
            "templates": [
                {
                    "template_id": item.template_id,
                    "name": item.name,
                    "permissions": sorted(item.permissions),
                    "expires_in": item.expires_in,
                }
                for item in templates
            ],
        })

    def system_permissions(_data: dict[str, Any], headers: dict[str, str], now: int | None):
        authentication = _service(context, "authentication")
        authorization_service = _service(context, "authorization_service")
        user_id = session_user(authentication, headers, now=now)
        if authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        authorization_service.require_team(user_id, SYSTEM_TEAM_ID, "users.read", now=now)
        return ok({"permissions": sorted(_service(context, "permissions").permission_nodes)})

    resources.register("system.server_info", description="Server manager protocol and signing identity", auto_grant=False) \
        .add_public("read", server_info)
    resources.register("system.key_status", description="Public key status", auto_grant=False) \
        .add_public("read", key_status)
    resources.register("system.authentication.github", description="GitHub token authentication", auto_grant=False) \
        .add_public("exchange", github_exchange)
    resources.register("system.authentication.github.device", description="GitHub device flow", auto_grant=False) \
        .add_public("start", github_device_start) \
        .add_public("poll", github_device_poll)
    resources.register("system.authentication.github.web", description="GitHub browser flow", auto_grant=False) \
        .add_public("start", github_web_start)
    resources.register("system.authentication.service", description="Service account credentials", auto_grant=False) \
        .add_public("exchange", service_exchange)
    resources.register("system.authentication.me", description="Current authenticated user") \
        .add_perm("read", auth_me)
    resources.register("system.authentication.me.authorization", description="Current authorization snapshot") \
        .add_perm("read", auth_me_authorization)
    resources.register("system.authentication.me.teams", description="Current accessible Teams") \
        .add_perm("read", auth_me_teams)
    resources.register("system.authentication.team_context", description="Selected Team context") \
        .add_perm("select", select_team_context)
    resources.register("system.authentication.session", description="Current session") \
        .add_perm("revoke", revoke_session)
    resources.register("system.platform.config", description="Platform mode") \
        .add_perm("read", platform_config)
    resources.register("system.overview", description="System overview") \
        .add_perm("read", overview)
    resources.register("system.operations.status", description="Global operation status") \
        .add_perm("read", operations_status)
    resources.register("system.permissions", description="Permission catalog") \
        .add_perm("read", list_permissions)
    resources.register("system.schema.permissions", description="System permission projection") \
        .add_perm("read", system_permissions)
