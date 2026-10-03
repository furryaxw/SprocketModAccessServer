import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class DownloadAuthorization:
    github_user_id: str
    package_id: str
    version: str
    expires_at: int


class DownloadTokenCodec:
    def __init__(self, secret: bytes, *, max_ttl: int = 120):
        if not secret or max_ttl < 1:
            raise ValueError("download token configuration is invalid")
        self.secret = bytes(secret)
        self.max_ttl = max_ttl

    def issue(
            self,
            *,
            github_user_id: str,
            package_id: str,
            version: str,
            now: int | None = None,
            ttl: int | None = None,
    ) -> str:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        lifetime = self.max_ttl if ttl is None else ttl
        if not github_user_id or not package_id or not version or lifetime < 1 or lifetime > self.max_ttl:
            raise ValueError("download authorization claims are invalid")
        payload = {
            "uid": github_user_id,
            "pkg": package_id,
            "ver": version,
            "exp": timestamp + lifetime,
        }
        encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{_encode(signature)}"

    def verify(
            self,
            token: str,
            *,
            github_user_id: str,
            package_id: str,
            version: str,
            now: int | None = None,
    ) -> DownloadAuthorization:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        try:
            encoded, signature = token.split(".", 1)
            expected = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(_decode(signature), expected):
                raise ValueError
            payload = json.loads(_decode(encoded).decode("utf-8"))
            authorization = DownloadAuthorization(
                str(payload["uid"]), str(payload["pkg"]), str(payload["ver"]), int(payload["exp"]),
            )
        except (ValueError, TypeError, KeyError, IndexError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("download authorization is invalid") from None
        if authorization.expires_at <= timestamp:
            raise ValueError("download authorization has expired")
        if (
                authorization.github_user_id != github_user_id
                or authorization.package_id != package_id
                or authorization.version != version
        ):
            raise ValueError("download authorization does not match request")
        return authorization
