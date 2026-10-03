from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class ServerSettings:
    database_url: str = "sqlite:///data/access.db"
    object_storage_url: str = "file:./data/packages"
    platform_mode: str = "simple"
    auto_register_users: bool = True
    first_login_permission_template: str = "template.owner"
    new_user_permission_template: str = "template.user"
    service_accounts: tuple[tuple[str, str], ...] = ()
    # 服务端自己的公开 origin，用于索引里下载端点的绝对 URL；留空时由 HTTP 请求推出。
    public_base_url: str = ""
    # 允许把下载指向哪些源：裸主机名表示仅接受 https 并按主机名匹配，完整 origin 按 scheme/host/port 精确匹配。
    download_origins: tuple[str, ...] = ()

    @classmethod
    def from_environment(cls) -> "ServerSettings":
        database = os.environ.get("SMAS_DATABASE_URL", cls.database_url).strip()
        storage = os.environ.get("SMAS_OBJECT_STORAGE_URL", cls.object_storage_url).strip()
        mode = os.environ.get("SMAS_PLATFORM_MODE", cls.platform_mode).strip().lower()
        auto_register = _bool_env("SMAS_AUTO_REGISTER_USERS", cls.auto_register_users)
        first_template = os.environ.get(
            "SMAS_FIRST_LOGIN_PERMISSION_TEMPLATE",
            cls.first_login_permission_template,
        ).strip()
        user_template = os.environ.get(
            "SMAS_NEW_USER_PERMISSION_TEMPLATE",
            cls.new_user_permission_template,
        ).strip()
        service_accounts = _service_accounts_env("SMAS_SERVICE_ACCOUNTS")
        public_base_url = os.environ.get("SMAS_PUBLIC_BASE_URL", cls.public_base_url).strip().rstrip("/")
        download_origins = _origin_list_env("SMAS_DOWNLOAD_ORIGINS")
        if not database:
            raise ValueError("SMAS_DATABASE_URL must not be empty")
        if not storage:
            raise ValueError("SMAS_OBJECT_STORAGE_URL must not be empty")
        if mode not in {"simple", "complex"}:
            raise ValueError("SMAS_PLATFORM_MODE must be simple or complex")
        if not first_template:
            raise ValueError("SMAS_FIRST_LOGIN_PERMISSION_TEMPLATE must not be empty")
        if not user_template:
            raise ValueError("SMAS_NEW_USER_PERMISSION_TEMPLATE must not be empty")
        return cls(database, storage, mode, auto_register, first_template, user_template, service_accounts,
                   public_base_url, download_origins)

    def sqlite_path(self, base_dir: Path) -> Path:
        parsed = urlparse(self.database_url)
        if parsed.scheme != "sqlite" or parsed.netloc:
            raise ValueError("database URL is not a SQLite URL")
        raw = parsed.path.lstrip("/")
        if not raw:
            raise ValueError("SQLite database path is missing")
        path = Path(raw)
        return path if path.is_absolute() else base_dir / path

    def local_storage_path(self, base_dir: Path) -> Path:
        parsed = urlparse(self.object_storage_url)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            raise ValueError("object storage URL is not a local file URL")
        raw = parsed.path.lstrip("/")
        if not raw:
            raise ValueError("local object storage path is missing")
        path = Path(raw)
        return path if path.is_absolute() else base_dir / path

    def s3_storage_config(self, bucket_override: str | None = None) -> tuple[str, str, str | None]:
        parsed = urlparse(self.object_storage_url)
        if parsed.scheme not in {"s3", "s3+http", "s3+https"} or not parsed.netloc:
            raise ValueError("object storage URL is not an S3 URL")
        parts = parsed.path.strip("/").split("/", 1) if parsed.path.strip("/") else []
        if parsed.scheme == "s3":
            bucket, prefix = parsed.netloc, parsed.path.strip("/")
        else:
            bucket = bucket_override or (parts[0] if parts else "")
            prefix = parts[1] if len(parts) > 1 else ""
            if not bucket:
                raise ValueError("S3-compatible bucket is required")
        endpoint = None
        if parsed.scheme != "s3":
            endpoint = f"{'https' if parsed.scheme == 's3+https' else 'http'}://{parsed.netloc}"
        return bucket, prefix, endpoint


def _origin_list_env(name: str) -> tuple[str, ...]:
    """逗号分隔的下载源列表；重复项只留一次。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return ()
    origins = [item.strip().rstrip("/") for item in raw.split(",")]
    if any(not item for item in origins):
        raise ValueError(f"{name} entries must not be empty")
    return tuple(dict.fromkeys(origins))


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _service_accounts_env(name: str) -> tuple[tuple[str, str], ...]:
    """解析 `{service_id: secret}`。

    保留 id 必须非数字：真实身份是 GitHub 数字 id，数字保留 id 会与真人账号撞号。
    密钥是公开兑换接口的唯一凭据，因此要求足够长，使在线猜测不可行。
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    accounts: list[tuple[str, str]] = []
    for key, value in parsed.items():
        service_id = str(key).strip()
        if not service_id or not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} service ids and secrets must be non-empty strings")
        if service_id.isdigit():
            raise ValueError(f"{name} service ids must not be numeric")
        if len(value.strip()) < 32:
            raise ValueError(f"{name} secrets must be at least 32 characters")
        accounts.append((service_id, value))
    return tuple(sorted(accounts))
