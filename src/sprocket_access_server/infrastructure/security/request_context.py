from __future__ import annotations

from typing import Any

from ...domain.errors import ApiError


def bearer(headers: dict[str, str]) -> str:
    authorization = next(
        (str(value) for key, value in headers.items() if str(key).casefold() == "authorization"),
        "",
    )
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise ApiError(401, "invalid_session", "session is invalid or expired")
    return token.strip()


def session_user(authentication: Any, headers: dict[str, str], *, now: int | None = None) -> str:
    return authentication.authenticate_session(bearer(headers), now=now)


def platform_user(
        authentication: Any,
        platform: Any,
        headers: dict[str, str],
        *,
        now: int | None = None,
) -> tuple[str, dict[str, object]]:
    user_id = session_user(authentication, headers, now=now)
    user = platform.user(user_id) if platform is not None else None
    return user_id, user or {"github_user_id": user_id}


def team_context(
        authorization_service: Any,
        headers: dict[str, str],
        user_id: str,
        *,
        required: bool = True,
) -> str | None:
    if authorization_service is None:
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    team_id = next(
        (str(value).strip() for key, value in headers.items() if str(key).casefold() == "x-team-id"),
        "",
    )
    if team_id:
        authorization_service.context(user_id, team_id=team_id)
    if required and not team_id:
        raise ApiError(400, "team_context_required", "explicit Team context is required")
    return team_id or None


def require_context_team(headers: dict[str, str], expected_team_id: str) -> None:
    selected = next(
        (str(value).strip() for key, value in headers.items() if str(key).casefold() == "x-team-id"),
        "",
    )
    if selected != expected_team_id:
        raise ApiError(403, "permission_denied", f"{expected_team_id} workspace context is required")
