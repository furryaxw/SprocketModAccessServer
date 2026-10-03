from __future__ import annotations

from sprocket_access_server.domain.errors import ApiError
from sprocket_access_server.domain.resources import SYSTEM_TEAM_ID, TEMPLATE_TEAM_ID
from sprocket_access_server.infrastructure.permissions import PermissionCatalog
from sprocket_access_server.infrastructure.network import ResourceRegistry
from sprocket_access_server.modules.resource_handlers import refresh_permissions
from sprocket_access_server.modules.team.resources import register_team_resources
from sprocket_access_server.modules.permission_templates.resources import register_template_resources
from sprocket_access_server.modules.permission_assignments.resources import register_assignment_resources


class HarnessContextMixin:
    """Lifecycle wiring for the resource-dispatch test fixture."""

    def _sync_team_resources(self) -> None:
        if self.module_context is None or self.platform is None:
            return
        current_team_ids = {
            str(item["team_id"])
            for item in self.platform.teams()
            if str(item["team_id"]) not in {SYSTEM_TEAM_ID, TEMPLATE_TEAM_ID}
        }
        new_team_ids = sorted(current_team_ids - self._registered_team_ids)
        for team_id in new_team_ids:
            register_team_resources(self.module_context, team_id)
            register_template_resources(self.module_context, team_id)
            register_assignment_resources(self.module_context, team_id)
            # Runtime operation adapters are test-only; production modules own their registrations.
            from .harness_routes import register_team_runtime_resources
            register_team_runtime_resources(self, team_id)
            self._registered_team_ids.add(team_id)
        if new_team_ids:
            refresh_permissions(self.module_context)

    def _sync_runtime_services(self) -> None:
        if self.module_context is None:
            return
        self.module_context.services.update({
            "database": self.authorization.database if self.authorization is not None else None,
            "platform": self.platform,
            "authentication": self.authentication,
            "authorization": self.authorization,
            "authorization_service": self.authorization_service,
            "packages": self.packages,
            "publisher": self.publisher,
            "audit": self.audit,
            "permissions": self.permission_catalog,
            "key_issuer": self.key_issuer,
            "idempotency": self.idempotency,
            "permission_templates": self.permission_templates,
            "confirmations": self.confirmations,
            "upload_store": self.upload_store,
            "direct_storage": self.direct_storage,
            "signing_key": self.signing_key,
            "signing_key_id": self.signing_key_id,
            "server_info": self.server_info,
            "download_tokens": self.download_tokens,
            "rate_limiter": self.rate_limiter,
            "email_outbox": self.email_outbox,
        })
