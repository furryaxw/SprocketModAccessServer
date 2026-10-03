from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Public error contract: clients branch on these stable codes and statuses.
ERROR_CATALOG: dict[str, int] = {
    "admin_forbidden": 403,
    "admin_unavailable": 503,
    "admin_ui_unavailable": 503,
    "authorization_unavailable": 503,
    "confirmation_required": 400,
    "distribution_unavailable": 503,
    "github_identity_failed": 502,
    "github_identity_invalid": 502,
    "github_identity_unavailable": 503,
    "github_device_flow_unavailable": 503,
    "github_device_flow_invalid": 502,
    "github_device_flow_expired": 410,
    "github_oauth_unavailable": 503,
    "github_oauth_invalid": 400,
    "github_token_rejected": 401,
    "github_token_forbidden": 403,
    "grant_not_found": 404,
    "grant_scope_denied": 403,
    "idempotency_key_reused": 409,
    "invalid_github_token": 400,
    "invitation_invalid": 400,
    "invalid_idempotency_key": 400,
    "invalid_request": 400,
    "invalid_session": 401,
    "key_batch_not_found": 404,
    "key_not_found": 404,
    "key_unavailable": 409,
    "key_update_rejected": 409,
    "not_found": 404,
    "package_not_found": 404,
    "permission_denied": 403,
    "rate_limit_exceeded": 429,
    "registration_disabled": 403,
    "registration_unavailable": 503,
    "reserved_team": 403,
    "self_lockout": 403,
    "service_credential_rejected": 401,
    "signing_unavailable": 503,
    "system_account": 403,
    "system_denied": 403,
    "team_context_required": 400,
    "team_not_found": 404,
    "team_application_exists": 409,
    "team_access_denied": 403,
    "team_permission_denied": 403,
    "content_denied": 403,
    "template_not_found": 404,
}


@dataclass
class ApiError(Exception):
    status: int
    code: str
    message: str
    headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.status < 400 or self.status > 599:
            raise ValueError("API error status must be 4xx or 5xx")
        if not self.code or not self.code.replace("_", "").isalnum():
            raise ValueError("API error code is invalid")
        expected_status = ERROR_CATALOG.get(self.code)
        if expected_status is None:
            raise ValueError("API error code is not in the error catalog")
        if expected_status != self.status:
            raise ValueError("API error status does not match the error catalog")
        if not self.message.strip():
            raise ValueError("API error message is required")
        Exception.__init__(self, self.message)

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}
