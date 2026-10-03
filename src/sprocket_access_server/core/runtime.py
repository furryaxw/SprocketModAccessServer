from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from .contracts import ModuleContext, attach_modules, register_modules
from ..domain.protocol import ServerInfo
from ..infrastructure.configuration.settings import ServerSettings
from ..infrastructure.database import SchemaRegistry, SQLiteDatabase
from ..infrastructure.events import InfrastructureEventBus
from ..infrastructure.logging import configure_logging
from ..infrastructure.messaging.email import build_email_sender
from ..infrastructure.messaging.email_outbox import SQLiteEmailOutbox
from ..infrastructure.network import ResourceRegistry
from ..infrastructure.permissions import PermissionCatalog
from ..infrastructure.security.github import GitHubApiIdentityProvider
from ..infrastructure.security.sessions import SQLiteSessionStore
from ..infrastructure.security.signing import load_private_key, public_identity
from ..infrastructure.storage.objects import LocalFileObjectStorage, S3ObjectStorage
from ..infrastructure.storage.uploads import SQLiteUploadStore
from ..infrastructure.utilities.confirmations import SQLiteConfirmationStore
from ..infrastructure.utilities.idempotency import SQLiteIdempotencyStore
from ..modules.audit.store import SQLiteAuditStore
from ..modules.keys.store import KeyIssuer
from ..modules.packages.download_tokens import DownloadTokenCodec
from ..modules.packages.publication import PackagePublisher
from ..modules.packages.store import SQLitePackageStore
from ..modules.permission_assignments.evaluator import AuthorizationService
from ..modules.permission_assignments.store import SQLiteAuthorizationStore
from ..modules.permission_templates.store import SQLiteGrantTemplateStore
from ..modules.registry import registered_modules
from ..modules.resource_handlers import refresh_permissions
from ..modules.system.authentication import AuthenticationService, RegistrationPolicy
from ..modules.system.platform import PlatformStore
from ..modules.system.provisioning import SystemProvisioning
from ..presentation.asgi import create_app

logger = logging.getLogger(__name__)


def build_app(*, base_dir: Path | None = None):
    root = (base_dir or Path.cwd()).expanduser()
    settings = ServerSettings.from_environment()
    log_level = os.environ.get("SMAS_LOG_LEVEL", "INFO")
    configure_logging(log_level)
    logger.debug("runtime build start base_dir=%s log_level=%s", root, log_level)
    database = SQLiteDatabase(settings.sqlite_path(root))
    logger.debug("database configured path=%s platform_mode=%s", database.path, settings.platform_mode)
    module_context = ModuleContext(
        database=SchemaRegistry(database),
        events=InfrastructureEventBus(),
        resources=ResourceRegistry(),
    )
    registered = register_modules(module_context, registered_modules())
    platform = PlatformStore(database, settings.platform_mode)
    provisioning = SystemProvisioning(database, settings.platform_mode)
    # 服务号行先于任何兑换建立，管理员才能在授权面给它分配节点。
    for service_id, _secret in settings.service_accounts:
        provisioning.ensure_service_account(service_id)
    logger.debug("modules registered count=%d permission_nodes=%d service_accounts=%d", len(registered),
                 len(module_context.resources.permission_nodes()), len(settings.service_accounts))

    pepper = _required_secret("SMAS_SESSION_PEPPER")
    sessions = SQLiteSessionStore(database, pepper)
    authorization = SQLiteAuthorizationStore(database, events=module_context.events)
    authorization_service = AuthorizationService(database)
    packages = SQLitePackageStore(database, events=module_context.events)
    storage = _object_storage(settings, root)
    # 下载源 = 显式声明的 + 存储自己会指向的源（对象存储就是桶的 origin）。
    download_origins = tuple(dict.fromkeys(
        [*settings.download_origins, *([storage.download_origin] if storage.download_origin else [])]
    ))
    publisher = PackagePublisher(packages, storage, download_base_url=settings.public_base_url)
    upload_store = SQLiteUploadStore(database)
    key_issuer = KeyIssuer(database, authorization, _required_secret("SMAS_KEY_PEPPER"), events=module_context.events)
    download_tokens = DownloadTokenCodec(
        _required_secret("SMAS_DOWNLOAD_TOKEN_SECRET"),
        max_ttl=int(os.environ.get("SMAS_DOWNLOAD_TOKEN_TTL", "120")),
    )
    idempotency = SQLiteIdempotencyStore(database)
    audit = SQLiteAuditStore(database)
    email_outbox = SQLiteEmailOutbox(database, audit)
    email_sender = build_email_sender() if _smtp_requested() else None
    email_poll_interval = _positive_float("SMAS_EMAIL_POLL_INTERVAL", "15")
    permission_catalog = PermissionCatalog(
        database,
        resource_permissions=module_context.resources.permission_nodes(),
    )
    permission_templates = SQLiteGrantTemplateStore(database, events=module_context.events)
    confirmations = SQLiteConfirmationStore(database)
    github = GitHubApiIdentityProvider(
        api_url=os.environ.get("SMAS_GITHUB_API_URL", "https://api.github.com"),
        timeout=int(os.environ.get("SMAS_GITHUB_TIMEOUT", "10")),
    )
    auth = AuthenticationService(
        github,
        sessions,
        session_ttl=int(os.environ.get("SMAS_SESSION_TTL", str(30 * 24 * 60 * 60))),
        ownership=database,
        github_device_client_id=os.environ.get("SMAS_GITHUB_CLIENT_ID", "").strip(),
        github_client_secret=os.environ.get("SMAS_GITHUB_CLIENT_SECRET", ""),
        github_callback_url=os.environ.get("SMAS_GITHUB_CALLBACK_URL", ""),
        provisioning=provisioning,
        registration_policy=RegistrationPolicy(
            auto_register_users=settings.auto_register_users,
            first_login_permission_template=settings.first_login_permission_template,
            new_user_permission_template=settings.new_user_permission_template,
        ),
        service_accounts=dict(settings.service_accounts),
    )
    signing_key = load_private_key()
    signing_key_id = os.environ.get("SMAS_SIGNING_KEY_ID", "").strip()
    if not signing_key_id:
        raise RuntimeError("SMAS_SIGNING_KEY_ID is required")
    server_info = ServerInfo(
        os.environ.get("SMAS_SERVER_ID", "development-server"),
        os.environ.get("SMAS_SERVER_NAME", "Sprocket Developer Server"),
        public_identity(signing_key, signing_key_id),
        {
            "trust_methods": ["https", "manual"],
            "encodings": ["base64url", "base64", "hex"],
            "auth_methods": auth.github_auth_methods(),
        },
        download_origins,
    )
    module_context.services.update({
        "settings": settings,
        "events": module_context.events,
        "database": database,
        "platform": platform,
        "provisioning": provisioning,
        "authentication": auth,
        "authorization": authorization,
        "authorization_service": authorization_service,
        "packages": packages,
        "publisher": publisher,
        "audit": audit,
        "permissions": permission_catalog,
        "server_info": server_info,
        "signing_key": signing_key,
        "signing_key_id": signing_key_id,
        "download_tokens": download_tokens,
        "idempotency": idempotency,
        "key_issuer": key_issuer,
        "permission_templates": permission_templates,
        "confirmations": confirmations,
        "upload_store": upload_store,
        "direct_storage": storage,
    })
    attach_modules(module_context, registered)
    refresh_permissions(module_context)
    return create_app(
        server_info,
        auth,
        email_outbox=email_outbox,
        email_sender=email_sender,
        email_poll_interval=email_poll_interval,
        module_context=module_context,
        modules=registered,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start Sprocket Mod Access Server")
    parser.add_argument("--host", default=os.environ.get("SMAS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SMAS_PORT", "8787")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    configure_logging(os.environ.get("SMAS_LOG_LEVEL", "INFO"))
    uvicorn.run(
        "sprocket_access_server.core.runtime:build_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_config=None,
        reload=args.reload,
        reload_dirs=["sprocket_access_server"],
    )
    return 0


def _object_storage(settings: ServerSettings, root: Path):
    if settings.object_storage_url.startswith(("s3://", "s3+http://", "s3+https://")):
        bucket, prefix, endpoint = settings.s3_storage_config(os.environ.get("SMAS_S3_BUCKET", "").strip() or None)
        return S3ObjectStorage(
            bucket,
            prefix=prefix,
            endpoint_url=endpoint,
            region=os.environ.get("SMAS_S3_REGION", "us-east-1"),
            force_path_style=os.environ.get("SMAS_S3_FORCE_PATH_STYLE", "1").strip().lower()
                             not in {"0", "false", "no"},
            download_url_ttl=int(os.environ.get("SMAS_DOWNLOAD_URL_TTL", "300")),
        )
    return LocalFileObjectStorage(settings.local_storage_path(root))


def _positive_float(name: str, default: str) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _required_secret(name: str) -> bytes:
    value = os.environ.get(name, "").encode("utf-8")
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _smtp_requested() -> bool:
    return bool(os.environ.get("SMAS_SMTP_HOST", "").strip() or os.environ.get("SMAS_EMAIL_FROM", "").strip())


if __name__ == "__main__":
    raise SystemExit(main())
