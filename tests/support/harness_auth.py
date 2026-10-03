from __future__ import annotations

from sprocket_access_server.domain.errors import ApiError


class HarnessAuthMixin:
    """Session and Team-context resolution shared by resource adapters."""

    def _bearer(self, headers: dict[str, str]) -> str:
        authorization = self._header(headers, "Authorization")
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            raise ApiError(401, "invalid_session", "session is invalid or expired")
        return token.strip()

    def _session_user(self, headers: dict[str, str], *, now: int | None) -> tuple[str, str]:
        token = self._bearer(headers)
        user_id = self.authentication.authenticate_session(token, now=now)
        return user_id, token

    def _team_id(self, headers: dict[str, str], user_id: str, *, required: bool = True) -> str | None:
        team_id = self._header(headers, "X-Team-Id").strip()
        if self.authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        if team_id:
            self.authorization_service.context(user_id, team_id=team_id)
        if required and not team_id:
            raise ApiError(400, "team_context_required", "explicit Team context is required")
        return team_id or None

    def authenticated_admin(self, headers: dict[str, str], *, now: int | None = None,
                            required_node: str | None = None) -> str:
        scheme, _, token = self._header(headers, "Authorization").partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            raise ApiError(401, "invalid_session", "session is invalid or expired")
        user_id = self.authentication.authenticate_session(token.strip(), now=now)
        if self.authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        context = self.authorization_service.context(user_id, now=now)
        if required_node is not None:
            if required_node.startswith("team."):
                parts = required_node.split(".")
                self.authorization_service.require_team(user_id, parts[1], ".".join(parts[2:]), now=now)
            else:
                self.authorization_service.require_system(user_id, required_node, now=now)
        elif not context.effective_permissions:
            raise ApiError(403, "system_denied", "system permission is required")
        return user_id

    def _platform_user(self, headers: dict[str, str], *, now: int | None) -> tuple[str, dict[str, object]]:
        user_id, _ = self._session_user(headers, now=now)
        if self.platform is None:
            raise ApiError(503, "admin_unavailable", "platform service is not configured")
        user = self.platform.user(user_id)
        if user is None:
            raise ApiError(403, "admin_forbidden", "platform user is not registered")
        return user_id, user

    def _platform_admin(self, headers: dict[str, str], *, now: int | None) -> str:
        user_id, _ = self._platform_user(headers, now=now)
        if self.authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        self.authorization_service.require_system(user_id, "system.teams.manage", now=now)
        return user_id

    def _reviewer(self, headers: dict[str, str], *, now: int | None) -> str:
        return self._platform_admin(headers, now=now)

    def _revoke_session(self, headers: dict[str, str], *, now: int | None):
        user_id, token = self._session_user(headers, now=now)
        self.authentication.revoke_session(token, now=now)
        if self.audit is not None:
            import time
            self.audit.record(actor=f"github:{user_id}", action="session.revoke", target="session", metadata={},
                              now=int(time.time()) if now is None else now)
        from sprocket_access_server.modules.http_common import ApiResponse
        return ApiResponse(200, {"ok": True})
