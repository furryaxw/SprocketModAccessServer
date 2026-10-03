from __future__ import annotations

import time
import logging
from typing import Any

from ..domain.errors import ApiError
from ..domain.permissions import evaluate
from ..infrastructure.network import ResourceMatch
from ..modules.permission_assignments.evaluator import AuthorizationService
from ..modules.system.authentication import AuthenticationService

logger = logging.getLogger(__name__)


def authorize_resource_operation(
        match: ResourceMatch,
        *,
        authentication: AuthenticationService,
        authorization: AuthorizationService | None,
        headers: dict[str, str],
        now: int | None,
        data: dict[str, object] | None = None,
) -> str | None:
    if not match.operation.requires_permission:
        logger.debug("resource authorization skipped public action=%s node=%s",
                     match.operation.action, match.definition.node)
        return None
    if authorization is None:
        logger.warning("resource authorization unavailable action=%s node=%s",
                       match.operation.action, match.definition.node)
        raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
    token = _bearer(headers)
    user_id = authentication.authenticate_session(token, now=now)
    timestamp = int(time.time()) if now is None or now <= 0 else now
    target_user_id = str((data or {}).get("user_id", "")).strip()
    if (
            match.definition.node in {"system.users", "team.system.users"}
            and match.operation.action in {"suspend", "activate", "set_permission_template"}
            and target_user_id
            and target_user_id == user_id
    ):
        return user_id
    team_id = _team_context_for_node(match.definition.node, headers)
    context = authorization.context(user_id, team_id=team_id, now=timestamp)
    requested = f"{match.definition.node}.{match.operation.action}"
    if not evaluate(context.permission_assignments, requested, now=timestamp).allowed:
        logger.warning("resource authorization denied user_id=%s permission=%s team_id=%s",
                       user_id, requested, team_id)
        raise ApiError(403, "permission_denied", "permission is required")
    logger.debug("resource authorization allowed user_id=%s permission=%s team_id=%s",
                 user_id, requested, team_id)
    return user_id


def _bearer(headers: dict[str, str]) -> str:
    authorization = next((str(value) for key, value in headers.items() if str(key).casefold() == "authorization"), "")
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise ApiError(401, "invalid_session", "session is invalid or expired")
    return token.strip()


def _team_context_for_node(node: str, headers: dict[str, str]) -> str | None:
    if node.startswith("team."):
        parts = node.split(".", 2)
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return None
