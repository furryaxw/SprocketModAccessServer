from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from sprocket_access_server.modules.system.authentication import AuthenticationService
from sprocket_access_server.modules.packages.publication import PackagePublisher
from sprocket_access_server.modules.packages.download_tokens import DownloadTokenCodec
from sprocket_access_server.domain.errors import ApiError
from sprocket_access_server.domain.protocol import ServerInfo
from sprocket_access_server.modules.audit.store import SQLiteAuditStore
from sprocket_access_server.modules.permission_assignments.store import SQLiteAuthorizationStore
from sprocket_access_server.modules.permission_assignments.evaluator import AuthorizationService
from sprocket_access_server.infrastructure.utilities.confirmations import SQLiteConfirmationStore
from sprocket_access_server.infrastructure.messaging.email_outbox import SQLiteEmailOutbox
from sprocket_access_server.modules.permission_templates.store import SQLiteGrantTemplateStore
from sprocket_access_server.infrastructure.utilities.idempotency import SQLiteIdempotencyStore
from sprocket_access_server.modules.keys.store import KeyIssuer
from sprocket_access_server.modules.packages.store import SQLitePackageStore
from sprocket_access_server.infrastructure.permissions import PermissionCatalog
from sprocket_access_server.modules.system.platform import PlatformStore
from sprocket_access_server.modules.system.provisioning import SystemProvisioning
from sprocket_access_server.infrastructure.utilities.rate_limit import InMemoryRateLimiter
from sprocket_access_server.infrastructure.storage.uploads import SQLiteUploadStore
from sprocket_access_server.modules.http_common import ApiResponse
from sprocket_access_server.core.contracts import ModuleContext, attach_modules, register_modules
from sprocket_access_server.infrastructure.database import SchemaRegistry
from sprocket_access_server.infrastructure.events import InfrastructureEventBus
from sprocket_access_server.infrastructure.network import ResourceRegistry
from sprocket_access_server.modules.registry import registered_modules
from .harness_context import HarnessContextMixin
from .harness_protocol import HarnessProtocolMixin
from .harness_auth import HarnessAuthMixin
from .harness_operations import (
    HarnessAssignmentOperationsMixin,
    HarnessKeyOperationsMixin,
    HarnessPackageOperationsMixin,
    HarnessTemplateOperationsMixin,
    HarnessTeamOperationsMixin,
)
from .harness_routes import register_static_test_resources


class ResourceDispatchHarness(
    HarnessContextMixin,
    HarnessProtocolMixin,
    HarnessAuthMixin,
    HarnessKeyOperationsMixin,
    HarnessPackageOperationsMixin,
    HarnessTemplateOperationsMixin,
    HarnessAssignmentOperationsMixin,
    HarnessTeamOperationsMixin,
):
    _VOLATILE_KEY_BATCH_TTL = 600

    def __init__(
            self,
            server_info: ServerInfo,
            authentication: AuthenticationService,
            *,
            key_issuer: KeyIssuer | None = None,
            idempotency: SQLiteIdempotencyStore | None = None,
            authorization: SQLiteAuthorizationStore | None = None,
            packages: SQLitePackageStore | None = None,
            audit: SQLiteAuditStore | None = None,
            publisher: PackagePublisher | None = None,
            download_tokens: DownloadTokenCodec | None = None,
            rate_limiter: InMemoryRateLimiter | None = None,
            upload_store: SQLiteUploadStore | None = None,
            direct_storage: Any | None = None,
            signing_key: Any | None = None,
            signing_key_id: str = "",
            permission_templates: SQLiteGrantTemplateStore | None = None,
            confirmations: SQLiteConfirmationStore | None = None,
            platform: PlatformStore | None = None,
            email_outbox: SQLiteEmailOutbox | None = None,
            permission_catalog: PermissionCatalog | None = None,
            authorization_service: AuthorizationService | None = None,
    ):
        self.server_info = server_info
        self.authentication = authentication
        self.key_issuer = key_issuer
        self.idempotency = idempotency
        self.authorization = authorization
        self.packages = packages
        self.audit = audit
        self.publisher = publisher
        self.download_tokens = download_tokens
        self.rate_limiter = rate_limiter
        self.upload_store = upload_store
        self.direct_storage = direct_storage
        self.signing_key = signing_key
        self.signing_key_id = signing_key_id
        self.permission_templates = permission_templates
        self.confirmations = confirmations
        self.platform = platform
        self.provisioning = (
            SystemProvisioning(platform.database, platform.mode) if platform is not None else None
        )
        self.email_outbox = email_outbox
        self.permission_catalog = permission_catalog
        self.authorization_service = authorization_service or (
            AuthorizationService(authorization.database) if authorization is not None else None
        )
        self._volatile_key_batch_lock = threading.Lock()
        self._volatile_key_batch_responses: dict[tuple[str, str], tuple[int, str, dict[str, object]]] = {}
        if authorization is not None:
            self.module_context = ModuleContext(
                database=SchemaRegistry(authorization.database),
                events=InfrastructureEventBus(),
                resources=ResourceRegistry(),
            )
            self.module_context.services.update({
                "database": authorization.database,
                "platform": platform,
                "provisioning": self.provisioning,
                "authentication": authentication,
                "authorization": authorization,
                "authorization_service": self.authorization_service,
                "packages": packages,
                "publisher": publisher,
                "audit": audit,
                "permissions": permission_catalog,
                "key_issuer": key_issuer,
                "idempotency": idempotency,
                "permission_templates": permission_templates,
                "confirmations": confirmations,
                "upload_store": upload_store,
                "direct_storage": direct_storage,
                "signing_key": signing_key,
                "signing_key_id": signing_key_id,
                "server_info": server_info,
            })
            registered = register_modules(self.module_context, registered_modules())
            attach_modules(self.module_context, registered)
            self.resources = self.module_context.resources
            register_static_test_resources(self)
        else:
            self.module_context = None
            self.resources = ResourceRegistry()
        self._registered_team_ids: set[str] = set()
        self._sync_team_resources()

    def dispatch(
            self,
            action: str,
            node: str,
            *,
            body: bytes = b"",
            data: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            now: int | None = None,
    ) -> ApiResponse:
        try:
            payload: dict[str, Any] = {}
            if body:
                payload.update(self._json_body(body))
            if data:
                payload.update(data)
            self._sync_runtime_services()
            self._sync_team_resources()
            return self.resources.dispatch(action, node, data=payload, headers=headers or {}, now=now)
        except ApiError as exc:
            return ApiResponse(exc.status, exc.payload(), dict(exc.headers))
        except KeyError:
            return ApiResponse(404, {"code": "not_found", "message": "resource was not found"})
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return ApiResponse(400, {"code": "invalid_request", "message": str(exc)})







