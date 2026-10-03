from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ...core.ports import GitHubIdentity, GitHubIdentityError

logger = logging.getLogger(__name__)


class GitHubApiIdentityProvider:
    def __init__(self, *, api_url: str = "https://api.github.com", timeout: int = 10):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def verify_access_token(self, access_token: str) -> GitHubIdentity:
        logger.debug("github identity request start api_url=%s token_length=%d", self.api_url, len(access_token))
        request = Request(
            self.api_url + "/user",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "SprocketModAccessServer/1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(64 * 1024 + 1)
        except HTTPError as exc:
            logger.warning("github identity request http_error status=%d", exc.code)
            if exc.code in (401, 403):
                raise GitHubIdentityError(
                    "GitHub rejected the access token (check token validity and permissions)",
                    status=exc.code,
                ) from exc
            raise GitHubIdentityError(
                f"GitHub identity request failed with HTTP {exc.code}", status=exc.code
            ) from exc
        except URLError as exc:
            logger.warning("github identity request url_error reason=%s", exc.reason)
            raise GitHubIdentityError(
                "GitHub identity service is unavailable", unavailable=True
            ) from exc
        if len(body) > 64 * 1024:
            raise ValueError("GitHub identity response is too large")
        try:
            payload = json.loads(body.decode("utf-8"))
            user_id = payload["id"]
            login = str(payload.get("login", ""))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            logger.warning("github identity response parse failed")
            raise ValueError("GitHub identity response is invalid") from None
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
            raise ValueError("GitHub identity response is invalid")
        identity = GitHubIdentity(str(user_id), login, str(payload.get("email") or "").strip())
        logger.debug("github identity request completed user_id=%s login=%s email_present=%s",
                     identity.user_id, identity.login, bool(identity.email))
        return identity

    def start_device_flow(self, client_id: str, *, scope: str = "read:user") -> dict[str, object]:
        return self._oauth_form("https://github.com/login/device/code", {"client_id": client_id, "scope": scope})

    def poll_device_flow(self, client_id: str, device_code: str) -> dict[str, object]:
        return self._oauth_form("https://github.com/login/oauth/access_token",
                                {"client_id": client_id, "device_code": device_code,
                                 "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})

    def exchange_web_code(self, client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict[str, object]:
        return self._oauth_form("https://github.com/login/oauth/access_token",
                                {"client_id": client_id, "client_secret": client_secret, "code": code,
                                 "redirect_uri": redirect_uri})

    def _oauth_form(self, url: str, values: dict[str, str]) -> dict[str, object]:
        logger.debug("github oauth form request start endpoint=%s fields=%s", url, sorted(values))
        request = Request(url, data=urlencode(values).encode("ascii"), method="POST",
                          headers={"Accept": "application/json", "User-Agent": "SprocketModAccessServer/1",
                                   "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read(64 * 1024 + 1).decode("utf-8"))
        except (HTTPError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("github oauth form request failed endpoint=%s error_type=%s", url, type(exc).__name__)
            raise GitHubIdentityError("GitHub device authorization is unavailable", unavailable=True) from exc
        if not isinstance(payload, dict):
            raise ValueError("GitHub device authorization response is invalid")
        logger.debug("github oauth form request completed endpoint=%s response_keys=%s", url, sorted(payload))
        return payload
