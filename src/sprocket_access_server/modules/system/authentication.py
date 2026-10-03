from __future__ import annotations

import hmac
import secrets
import threading
import time
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

from ...core.ports import GitHubIdentityError, GitHubIdentityProvider
from ...domain.errors import ApiError
from ...infrastructure.database import SQLiteDatabase
from ...infrastructure.security.sessions import SQLiteSessionStore
from ...modules.system.provisioning import SystemProvisioning

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthResult:
    token: str
    github_user_id: str
    created_at: int
    expires_at: int

    def payload(self) -> dict[str, str | int]:
        return {
            "token": self.token,
            "github_user_id": self.github_user_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class RegistrationPolicy:
    auto_register_users: bool = True
    first_login_permission_template: str = "template.owner"
    new_user_permission_template: str = "template.user"


class AuthenticationService:
    def __init__(
            self,
            github: GitHubIdentityProvider,
            sessions: SQLiteSessionStore,
            *,
            session_ttl: int = 30 * 24 * 60 * 60,
            ownership: SQLiteDatabase | None = None,
            github_device_client_id: str = "",
            github_client_secret: str = "",
            github_callback_url: str = "",
            provisioning: SystemProvisioning | None = None,
            registration_policy: RegistrationPolicy = RegistrationPolicy(),
            service_accounts: Mapping[str, str] | None = None,
    ):
        if session_ttl < 1:
            raise ValueError("session TTL must be positive")
        self.github = github
        self.sessions = sessions
        self.session_ttl = session_ttl
        self.ownership = ownership
        self.provisioning = provisioning
        self.registration_policy = registration_policy
        self.service_accounts = dict(service_accounts or {})
        self.github_device_client_id = github_device_client_id.strip()
        self.github_client_secret = github_client_secret.strip()
        self.github_callback_url = github_callback_url.strip()
        self._device_lock = threading.Lock()
        self._device_codes: dict[str, tuple[str, int]] = {}
        self._web_states: dict[str, int] = {}

    # 这两个表由公开动作（web/device start）写入，必须自带上限与过期清理，
    # 否则谁都能靠反复 start 把内存堆起来。
    _MAX_PENDING_FLOWS = 512

    @classmethod
    def _prune_flows(cls, store: dict[str, Any], *, now: int) -> None:
        expired = [
            key for key, value in store.items()
            if (value[1] if isinstance(value, tuple) else value) <= now
        ]
        for key in expired:
            store.pop(key, None)
        while len(store) >= cls._MAX_PENDING_FLOWS:
            store.pop(next(iter(store)), None)

    def start_github_web_flow(self, *, now: int | None = None) -> dict[str, object]:
        logger.debug("github web flow start client_id_configured=%s secret_configured=%s callback_configured=%s",
                     bool(self.github_device_client_id), bool(self.github_client_secret), bool(self.github_callback_url))
        if not self.github_device_client_id or not self.github_client_secret or not self.github_callback_url:
            raise ApiError(503, "github_oauth_unavailable", "GitHub OAuth Web Flow is not configured")
        timestamp = int(time.time()) if now is None or now <= 0 else now
        state = secrets.token_urlsafe(32)
        self._prune_flows(self._web_states, now=timestamp)
        self._web_states[state] = timestamp + 600
        logger.debug("github web flow state created state_length=%d expires_at=%d", len(state), timestamp + 600)
        query = urlencode(
            {"client_id": self.github_device_client_id, "redirect_uri": self.github_callback_url, "scope": "read:user",
             "state": state})
        return {"authorization_url": f"https://github.com/login/oauth/authorize?{query}",
                "callback_url": self.github_callback_url,
                "state": state, "expires_in": 600}

    def github_auth_methods(self) -> dict[str, object]:
        return {
            "github": {
                "web": bool(
                    self.github_device_client_id
                    and self.github_client_secret
                    and self.github_callback_url
                    and hasattr(self.github, "exchange_web_code")
                ),
                "device": bool(
                    self.github_device_client_id
                    and hasattr(self.github, "start_device_flow")
                    and hasattr(self.github, "poll_device_flow")
                ),
                "token_exchange": True,
            }
        }

    def complete_github_web_flow(self, code: str, state: str, *, now: int | None = None) -> AuthResult:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        expires = self._web_states.pop(state, None)
        logger.debug("github web callback received code_present=%s code_length=%d state_present=%s state_length=%d state_known=%s",
                     bool(code), len(code), bool(state), len(state), expires is not None)
        if not code or not state or expires is None or expires <= timestamp:
            raise ApiError(400, "github_oauth_invalid", "GitHub OAuth authorization is invalid or expired")
        if not self.github_client_secret or not hasattr(self.github, "exchange_web_code"):
            raise ApiError(503, "github_oauth_unavailable", "GitHub OAuth Web Flow is not configured")
        try:
            result = self.github.exchange_web_code(self.github_device_client_id, self.github_client_secret, code,
                                                   self.github_callback_url)
        except GitHubIdentityError as exc:
            logger.warning("github web code exchange failed unavailable=%s status=%s", exc.unavailable, exc.status)
            raise ApiError(503, "github_oauth_unavailable", str(exc)) from exc
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            logger.warning("github web code exchange returned no access token result_keys=%s", sorted(result))
            raise ApiError(400, "github_oauth_invalid", "GitHub OAuth authorization was rejected")
        logger.debug("github web code exchange succeeded access_token_length=%d", len(token))
        return self.exchange_github_token(token, now=timestamp)

    def start_github_device_flow(self, *, now: int | None = None) -> dict[str, object]:
        logger.debug("github device flow start client_id_configured=%s provider_supported=%s",
                     bool(self.github_device_client_id), hasattr(self.github, "start_device_flow"))
        if not self.github_device_client_id or not hasattr(self.github, "start_device_flow"):
            raise ApiError(503, "github_device_flow_unavailable", "GitHub Device Flow is not configured")
        try:
            result = self.github.start_device_flow(self.github_device_client_id)
        except GitHubIdentityError as exc:
            raise ApiError(503, "github_device_flow_unavailable", str(exc)) from exc
        device_code = result.get("device_code")
        if not isinstance(device_code, str) or not device_code:
            raise ApiError(502, "github_device_flow_invalid", "GitHub returned an invalid device code")
        timestamp = int(time.time()) if now is None or now <= 0 else now
        expires_in = int(result.get("expires_in", 900))
        with self._device_lock:
            self._prune_flows(self._device_codes, now=timestamp)
            self._device_codes[secrets.token_urlsafe(24)] = (device_code, timestamp + max(60, min(expires_in, 900)))
            handle = next(reversed(self._device_codes))
        return {"flow_id": handle, "user_code": result.get("user_code"),
                "verification_uri": result.get("verification_uri") or result.get("verification_uri_complete"),
                "interval": max(2, int(result.get("interval", 5))), "expires_in": expires_in}

    def poll_github_device_flow(self, flow_id: str, *, now: int | None = None) -> AuthResult | dict[str, str]:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self._device_lock:
            flow = self._device_codes.get(flow_id)
        logger.debug("github device flow poll flow_id_length=%d flow_known=%s", len(flow_id), flow is not None)
        if flow is None or flow[1] <= timestamp:
            raise ApiError(410, "github_device_flow_expired", "GitHub device authorization expired")
        try:
            result = self.github.poll_device_flow(self.github_device_client_id, flow[0])
        except GitHubIdentityError as exc:
            raise ApiError(503, "github_device_flow_unavailable", str(exc)) from exc
        error = result.get("error")
        logger.debug("github device flow poll response error=%s access_token_present=%s", error, bool(result.get("access_token")))
        if error in {"authorization_pending", "slow_down"}:
            return {"status": "pending"}
        if error in {"access_denied", "expired_token"}:
            raise ApiError(410, "github_device_flow_expired", "GitHub device authorization was not completed")
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise ApiError(502, "github_device_flow_invalid", "GitHub returned an invalid access token")
        with self._device_lock:
            self._device_codes.pop(flow_id, None)
        return self.exchange_github_token(token, now=timestamp)

    def exchange_github_token(self, access_token: str, *, now: int | None = None) -> AuthResult:
        logger.debug("github token exchange start access_token_present=%s access_token_length=%d",
                     bool(access_token.strip()), len(access_token))
        if not access_token.strip() or len(access_token) > 512:
            raise ApiError(400, "invalid_github_token", "GitHub access token is invalid")
        try:
            identity = self.github.verify_access_token(access_token)
        except GitHubIdentityError as exc:
            logger.warning("github identity verification failed unavailable=%s status=%s", exc.unavailable, exc.status)
            if exc.unavailable:
                raise ApiError(503, "github_identity_unavailable", str(exc)) from exc
            if exc.status == 401:
                raise ApiError(401, "github_token_rejected", str(exc)) from exc
            # GitHub 用 403 同时表示权限不足与限流（含二级限流）：令牌本身可能仍然有效，
            # 因此不能和 401 合并成"令牌被拒"，客户端需要能区分。
            if exc.status == 403:
                raise ApiError(403, "github_token_forbidden", str(exc)) from exc
            raise ApiError(502, "github_identity_failed", str(exc)) from exc
        except Exception as exc:
            logger.exception("github identity verification raised unexpected error")
            raise ApiError(502, "github_identity_failed", "GitHub identity verification failed") from exc
        if not identity.user_id.isdigit():
            raise ApiError(502, "github_identity_invalid", "GitHub returned an invalid user identity")
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if self.provisioning is None:
            raise ApiError(503, "registration_unavailable", "user provisioning is not configured")
        is_new_user = not self.provisioning.user_exists(identity.user_id)
        logger.debug("github identity verified user_id=%s login=%s new_user=%s auto_register=%s",
                     identity.user_id, identity.login, is_new_user, self.registration_policy.auto_register_users)
        if is_new_user and not self.registration_policy.auto_register_users:
            raise ApiError(403, "registration_disabled", "automatic user registration is disabled")
        self.provisioning.ensure_user(identity.user_id, identity.login, identity.email, now=timestamp)
        self.provisioning.assign_system_template(
            identity.user_id,
            self.registration_policy.new_user_permission_template,
            now=timestamp,
        )
        logger.debug("system user template assigned user_id=%s template_id=%s",
                     identity.user_id, self.registration_policy.new_user_permission_template)
        if self.ownership is not None:
            claimed = self.ownership.claim_first_owner(
                identity.user_id,
                now=timestamp,
                owner_template_id=self.registration_policy.first_login_permission_template,
            )
            logger.debug("first-owner claim evaluated user_id=%s claimed=%s", identity.user_id, claimed)
        session = self.sessions.create(identity.user_id, ttl=self.session_ttl, now=timestamp)
        logger.debug("github token exchange completed user_id=%s session_created=true expires_at=%s",
                     identity.user_id, session.get("expires_at"))
        return AuthResult(
            token=str(session["token"]),
            github_user_id=identity.user_id,
            created_at=int(session["created_at"]),
            expires_at=int(session["expires_at"]),
        )

    # 未知 service_id 也走一次定时安全比较，比较对象长度固定，避免用耗时区分
    # "服务号不存在"与"密钥错误"。
    _SERVICE_CREDENTIAL_PLACEHOLDER = "service-credential-placeholder"

    def exchange_service_credential(self, service_id: str, secret: str, *, now: int | None = None) -> AuthResult:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        candidate_id = service_id.strip()
        expected = self.service_accounts.get(candidate_id)
        compared = self._SERVICE_CREDENTIAL_PLACEHOLDER if expected is None else expected
        matched = hmac.compare_digest(compared.encode("utf-8"), secret.encode("utf-8"))
        if expected is None or not matched:
            logger.warning("service credential rejected service_id=%s", candidate_id or None)
            raise ApiError(401, "service_credential_rejected", "service credential is invalid")
        if self.provisioning is None:
            raise ApiError(503, "registration_unavailable", "user provisioning is not configured")
        row = self.provisioning.ensure_service_account(candidate_id, now=timestamp)
        if str(row.get("status", "")) != "active":
            logger.warning("service credential rejected service_id=%s inactive=true", candidate_id)
            raise ApiError(401, "service_credential_rejected", "service credential is invalid")
        session = self.sessions.create(candidate_id, ttl=self.session_ttl, now=timestamp)
        logger.debug("service credential exchange completed service_id=%s expires_at=%s",
                     candidate_id, session.get("expires_at"))
        return AuthResult(
            token=str(session["token"]),
            github_user_id=candidate_id,
            created_at=int(session["created_at"]),
            expires_at=int(session["expires_at"]),
        )

    def authenticate_session(self, token: str, *, now: int | None = None) -> str:
        try:
            return self.sessions.authenticate(token, now=now)
        except ValueError as exc:
            raise ApiError(401, "invalid_session", "session is invalid or expired") from exc

    def revoke_session(self, token: str, *, now: int | None = None) -> None:
        try:
            user_id = self.sessions.authenticate(token, now=now)
            self.sessions.revoke(token, now=now)
        except ValueError as exc:
            raise ApiError(401, "invalid_session", "session is invalid or expired") from exc
        del user_id
