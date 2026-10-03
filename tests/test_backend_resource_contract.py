from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.domain.errors import ApiError
from sprocket_access_server.domain.protocol import ServerInfo
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.permissions import PermissionCatalog
from sprocket_access_server.infrastructure.security.sessions import SQLiteSessionStore
from sprocket_access_server.infrastructure.storage.objects import LocalFileObjectStorage
from sprocket_access_server.infrastructure.storage.uploads import SQLiteUploadStore
from sprocket_access_server.infrastructure.utilities.confirmations import SQLiteConfirmationStore
from sprocket_access_server.infrastructure.utilities.idempotency import SQLiteIdempotencyStore
from sprocket_access_server.modules.audit.store import SQLiteAuditStore
from sprocket_access_server.modules.keys.store import KeyIssuer
from sprocket_access_server.modules.packages.download_tokens import DownloadTokenCodec
from sprocket_access_server.modules.packages.publication import PackagePublisher
from sprocket_access_server.modules.packages.store import SQLitePackageStore
from sprocket_access_server.modules.permission_assignments.store import SQLiteAuthorizationStore
from sprocket_access_server.modules.permission_assignments.provisioning import (
    upsert_assignment_grant,
    upsert_template_grant,
)
from sprocket_access_server.modules.permission_templates.store import SQLiteGrantTemplateStore
from sprocket_access_server.modules.system.authentication import AuthenticationService
from sprocket_access_server.modules.system.platform import PlatformStore
from sprocket_access_server.modules.system.provisioning import SystemProvisioning
from tests.support.resource_harness import ResourceDispatchHarness


class FakeGitHub:
    def verify_access_token(self, _token: str):
        from sprocket_access_server.core.ports import GitHubIdentity

        return GitHubIdentity("123", "owner")


class BackendResourceContractTests(unittest.TestCase):
    def api(self, directory: str) -> ResourceDispatchHarness:
        database = SQLiteDatabase(Path(directory) / "access.db")
        sessions = SQLiteSessionStore(database, b"pepper")
        platform = PlatformStore(database, "simple")
        provisioning = SystemProvisioning(database, "simple")
        authentication = AuthenticationService(
            FakeGitHub(),
            sessions,
            session_ttl=100,
            ownership=database,
            provisioning=provisioning,
        )
        authorization = SQLiteAuthorizationStore(database)
        packages = SQLitePackageStore(database)
        return ResourceDispatchHarness(
            ServerInfo("server-1", "Test", None, {}),
            authentication,
            key_issuer=KeyIssuer(database, authorization, b"key-pepper"),
            idempotency=SQLiteIdempotencyStore(database),
            authorization=authorization,
            packages=packages,
            audit=SQLiteAuditStore(database),
            download_tokens=DownloadTokenCodec(b"download-secret"),
            upload_store=SQLiteUploadStore(database),
            direct_storage=LocalFileObjectStorage(Path(directory) / "objects"),
            publisher=PackagePublisher(packages, LocalFileObjectStorage(Path(directory) / "objects")),
            permission_templates=SQLiteGrantTemplateStore(database),
            confirmations=SQLiteConfirmationStore(database),
            permission_catalog=PermissionCatalog(database),
            platform=platform,
        )

    @staticmethod
    def headers(token: str, *, team_id: str = "template", request_key: str = "request") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-Team-Id": team_id,
            "Idempotency-Key": request_key,
        }

    def test_production_template_manage_updates_and_disables_record(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            api.key_issuer.database.claim_first_owner("123", now=1)
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            token = exchanged.payload["token"]
            created = api.dispatch(
                "manage",
                "team.template.permission_templates",
                data={
                    "name": "QA",
                    "permissions": ["team.template.packages.read"],
                    "expires_in": 3600,
                },
                headers=self.headers(token, request_key="template-create"),
                now=100,
            )
            template_id = created.payload["template_id"]
            with api.key_issuer.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(github_user_id,login_snapshot,created_at,updated_at) "
                    "VALUES('template-user','template-user',100,100)"
                )
                upsert_template_grant(
                    connection,
                    grant_id="template-grant",
                    user_id="template-user",
                    team_id="template",
                    template_id=template_id,
                    created_by="123",
                    now=100,
                )
            preview = api.dispatch(
                "manage",
                "team.template.permission_templates",
                data={
                    "template_id": template_id,
                    "name": "QA updated",
                    "permissions": [
                        "team.template.packages.read",
                        "team.template.packages.manage",
                    ],
                    "expires_in": None,
                    "preview": True,
                },
                headers={"Authorization": f"Bearer {token}", "X-Team-Id": "template"},
                now=101,
            )
            updated = api.dispatch(
                "manage",
                "team.template.permission_templates",
                data={
                    "template_id": template_id,
                    "name": "QA updated",
                    "permissions": [
                        "team.template.packages.read",
                        "team.template.packages.manage",
                    ],
                    "expires_in": None,
                },
                headers=self.headers(token, request_key="template-update"),
                now=101,
            )
            confirmation = api.dispatch(
                "manage",
                "team.template.permission_templates",
                data={"template_id": template_id, "mode": "confirm"},
                headers={"Authorization": f"Bearer {token}"},
                now=102,
            )
            disabled = api.dispatch(
                "manage",
                "team.template.permission_templates",
                data={
                    "template_id": template_id,
                    "status": "disabled",
                    "confirmation_token": confirmation.payload["confirmation_token"],
                },
                headers={
                    **self.headers(token, request_key="template-disable"),
                },
                now=103,
            )

        self.assertEqual(created.status, 201)
        self.assertEqual(preview.status, 200)
        self.assertEqual(preview.payload["diff"]["permissions_added"], ["team.template.packages.manage"])
        self.assertEqual(preview.payload["diff"]["affected_users"], ["template-user"])
        self.assertTrue(preview.payload["diff"]["access_impact"]["adds_access"])
        self.assertEqual(updated.status, 200)
        self.assertEqual(updated.payload["name"], "QA updated")
        self.assertEqual(updated.payload["permissions"], [
            "team.template.packages.manage",
            "team.template.packages.read",
        ])
        self.assertEqual(disabled.status, 200)
        self.assertEqual(disabled.payload["status"], "disabled")

    def test_production_assignment_manage_previews_and_persists_existing_grant(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            api.platform = PlatformStore(database, "simple")
            api.provisioning.ensure_user("123", "owner", now=2)
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(github_user_id,login_snapshot,created_at,updated_at) "
                    "VALUES('target','target',1,1)"
                )
            issued = api.key_issuer.create_batch(
                quantity=1,
                permissions=frozenset({"team.template.packages.read"}),
                expires_at=None,
                actor="123",
                team_id="template",
                now=10,
            )[0]
            grant = api.authorization.redeem_key(
                key_hash=api.key_issuer.hash_key(issued.plaintext),
                github_user_id="target",
                team_id="template",
                now=11,
            )
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=12,
            )
            token = exchanged.payload["token"]
            preview = api.dispatch(
                "manage",
                "team.template.permission_assignments",
                data={
                    "assignment_id": grant.grant_id,
                    "permissions": {
                        "team.template.packages.read": "allow",
                        "team.template.packages.manage": "allow",
                    },
                    "preview": True,
                },
                headers=self.headers(token, request_key="assignment-preview"),
                now=13,
            )
            changed = api.dispatch(
                "manage",
                "team.template.permission_assignments",
                data={
                    "assignment_id": grant.grant_id,
                    "permissions": {
                        "team.template.packages.read": "allow",
                        "team.template.packages.manage": "allow",
                    },
                },
                headers=self.headers(token, request_key="assignment-update"),
                now=13,
            )
            confirmation = api.dispatch(
                "manage",
                "team.template.permission_assignments",
                data={
                    "assignment_id": grant.grant_id,
                    "operation": "suspend",
                    "mode": "confirm",
                },
                headers={"Authorization": f"Bearer {token}", "X-Team-Id": "template"},
                now=14,
            )
            suspended = api.dispatch(
                "manage",
                "team.template.permission_assignments",
                data={
                    "assignment_id": grant.grant_id,
                    "mode": "suspend",
                    "confirmation_token": confirmation.payload["confirmation_token"],
                },
                headers=self.headers(token, request_key="assignment-suspend"),
                now=15,
            )
            stored = api.authorization.grants("target", team_id="template")[0]

        self.assertEqual(preview.status, 200)
        self.assertEqual(preview.payload["diff"]["permissions_added"],
                         ["team.template.packages.manage"])
        self.assertEqual(preview.payload["diff"]["affected_users"], ["target"])
        self.assertFalse(preview.payload["diff"]["effect_changed"])
        self.assertFalse(preview.payload["diff"]["priority_changed"])
        self.assertTrue(preview.payload["diff"]["access_impact"]["adds_access"])
        self.assertEqual(changed.status, 200)
        self.assertEqual(changed.payload["permissions"],
                         [
                             "team.template.packages.manage",
                             "team.template.packages.read",
                         ])
        self.assertEqual(stored.permissions,
                         frozenset({
                             "team.template.packages.manage",
                             "team.template.packages.read",
                         }))
        self.assertEqual(confirmation.status, 200)
        self.assertEqual(suspended.status, 200)
        self.assertEqual(suspended.payload["status"], "suspended")

    def test_system_only_resources_reject_non_system_context(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            api.key_issuer.database.claim_first_owner("123", now=1)
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            token = exchanged.payload["token"]
            teams = api.dispatch(
                "read",
                "system.teams",
                headers=self.headers(token, team_id="template"),
                now=101,
            )
            operations = api.dispatch(
                "read",
                "system.operations.status",
                headers=self.headers(token, team_id="template"),
                now=101,
            )
            applications = api.dispatch(
                "read",
                "system.team_applications",
                headers=self.headers(token, team_id="template"),
                now=101,
            )

        self.assertEqual((teams.status, operations.status, applications.status), (403, 403, 403))
        self.assertEqual(teams.payload["code"], "permission_denied")

    def test_archived_team_leaves_workspace_options_and_cannot_be_selected(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            api.platform = PlatformStore(database, "complex")
            api.provisioning = SystemProvisioning(database, "complex")
            api.module_context.services["platform"] = api.platform
            api.module_context.services["provisioning"] = api.provisioning
            api.provisioning.ensure_user("123", "owner", now=2)
            api.provisioning.ensure_user("456", "applicant", now=2)
            application = api.platform.create_application(
                "456", "Build Team", "", now=3,
            )
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            token = exchanged.payload["token"]
            approval = api.dispatch(
                "confirm",
                "system.team_applications",
                data={"application_id": application["application_id"], "operation": "approve"},
                headers=self.headers(token, team_id="system"),
                now=101,
            )
            approved = api.dispatch(
                "approve",
                "system.team_applications",
                data={
                    "application_id": application["application_id"],
                    "confirmation_token": approval["confirmation_token"],
                },
                headers=self.headers(token, team_id="system", request_key="team-approve"),
                now=102,
            )
            team_id = approved["team_id"]
            before = api.dispatch(
                "read",
                "system.authentication.me.teams",
                headers={"Authorization": f"Bearer {token}"},
                now=103,
            )
            reserved = api.dispatch(
                "archive",
                "system.teams",
                data={"team_id": "template", "confirmation_token": "unused"},
                headers=self.headers(token, team_id="system", request_key="reserved-archive"),
                now=104,
            )
            confirmation = api.dispatch(
                "confirm",
                "system.teams",
                data={"team_id": team_id},
                headers={"Authorization": f"Bearer {token}", "X-Team-Id": "system"},
                now=105,
            )
            archived = api.dispatch(
                "archive",
                "system.teams",
                data={"team_id": team_id, "confirmation_token": confirmation.payload["confirmation_token"]},
                headers=self.headers(token, team_id="system", request_key="team-archive"),
                now=106,
            )
            after = api.dispatch(
                "read",
                "system.authentication.me.teams",
                headers={"Authorization": f"Bearer {token}"},
                now=107,
            )
            selected = api.dispatch(
                "select",
                "system.authentication.team_context",
                data={"team_id": team_id},
                headers={"Authorization": f"Bearer {token}"},
                now=108,
            )

        self.assertEqual(before.status, 200)
        self.assertIn(team_id, [item["team_id"] for item in before.payload["teams"]])
        # 保留 Team 的状态变更在消费确认令牌之前就被拒绝。
        self.assertEqual(reserved.status, 403)
        self.assertEqual(reserved.payload["code"], "reserved_team")
        self.assertEqual(archived.status, 200)
        self.assertEqual(archived.payload["status"], "archived")
        self.assertEqual(after.status, 200)
        self.assertNotIn(team_id, [item["team_id"] for item in after.payload["teams"]])
        self.assertEqual(selected.status, 403)
        self.assertEqual(selected.payload["code"], "permission_denied")

    def test_team_application_approval_confirmation_returns_preview(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            api.platform = PlatformStore(database, "complex")
            api.provisioning = SystemProvisioning(database, "complex")
            api.module_context.services["platform"] = api.platform
            api.module_context.services["provisioning"] = api.provisioning
            api.provisioning.ensure_user("123", "owner", now=2)
            api.provisioning.ensure_user("456", "applicant", now=2)
            application = api.platform.create_application(
                "456", "Build Team", "", now=3,
            )
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            token = exchanged.payload["token"]
            confirmation = api.dispatch(
                "confirm",
                "system.team_applications",
                data={"application_id": application["application_id"], "operation": "approve"},
                headers=self.headers(token, team_id="system"),
                now=101,
            )

        preview = confirmation["preview"]
        self.assertEqual(preview["team"]["owner_user_id"], "456")
        self.assertIn("team.<new_team>.permission_templates", preview["resources"])
        self.assertEqual(
            [template["name"] for template in preview["templates"]],
            ["Admin", "Developer", "Owner", "Tester"],
        )

    def test_system_package_upload_is_not_registered_and_helper_rejects_system(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            api.key_issuer.database.claim_first_owner("123", now=1)
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            token = exchanged.payload["token"]
            response = api.dispatch(
                "create",
                "team.system.package_uploads",
                data={"package_id": "mod.example", "version": "1.0.0", "size": 1},
                headers=self.headers(token, team_id="system", request_key="system-upload"),
                now=101,
            )

            with self.assertRaises(ApiError) as raised:
                from sprocket_access_server.modules.packages.resources import _create_upload

                _create_upload(
                    api.module_context,
                    {"package_id": "mod.example", "version": "1.0.0", "size": 1},
                    self.headers(token, team_id="system"),
                    101,
                    team_id="system",
                )

        self.assertEqual(response.status, 404)
        self.assertEqual(raised.exception.code, "permission_denied")

    def test_system_users_support_server_side_filters(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(github_user_id,login_snapshot,display_name,status,created_at,updated_at) "
                    "VALUES('456','alice','Alice Example','active',2,2)"
                )
                connection.execute(
                    "INSERT INTO users(github_user_id,login_snapshot,display_name,status,created_at,updated_at) "
                    "VALUES('789','bob','Bob Example','suspended',3,3)"
                )
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            result = api.dispatch(
                "read",
                "system.users",
                data={"login": "ali", "status": "active"},
                headers=self.headers(exchanged.payload["token"], team_id="system"),
                now=101,
            )

        self.assertEqual(result.status, 200)
        self.assertEqual([item["github_user_id"] for item in result.payload["users"]], ["456"])

    def test_combined_user_profile_manage_updates_status_and_template_atomically(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(github_user_id,login_snapshot,status,created_at,updated_at) "
                    "VALUES('456','alice','active',2,2)"
                )
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            token = exchanged.payload["token"]
            confirmation = api.dispatch(
                "confirm",
                "system.users",
                data={"user_id": "456"},
                headers=self.headers(token, team_id="system"),
                now=101,
            )
            confirmation_token = confirmation.payload["confirmation_token"]
            response = api.dispatch(
                "manage",
                "system.users",
                data={
                    "user_id": "456",
                    "status": "suspended",
                    "permission_template": "User",
                    "confirmation_token": confirmation_token,
                },
                headers=self.headers(token, team_id="system", request_key="profile-update"),
                now=102,
            )

            self.assertEqual(response.status, 200)
            self.assertEqual(response.payload["status"], "suspended")
            with database.transaction() as connection:
                row = connection.execute(
                    "SELECT status FROM users WHERE github_user_id=?", ("456",)
                ).fetchone()
                grant = connection.execute(
                    "SELECT source_type, source_id FROM grants "
                    "WHERE github_user_id=? AND team_id='system'",
                    ("456",),
                ).fetchone()
        self.assertEqual(row["status"], "suspended")
        self.assertEqual(grant["source_type"], "permission_template")
        self.assertEqual(grant["source_id"], "User")

    def test_user_status_change_rejects_self_and_builtin_system_account(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            token = exchanged.payload["token"]
            # 目标保护在消费确认令牌之前生效，因此这里传的是无效令牌。
            self_rejection = api.dispatch(
                "suspend",
                "system.users",
                data={"user_id": "123", "confirmation_token": "unused"},
                headers=self.headers(token, team_id="system", request_key="self-suspend"),
                now=101,
            )
            system_rejection = api.dispatch(
                "suspend",
                "system.users",
                data={"user_id": "system", "confirmation_token": "unused"},
                headers=self.headers(token, team_id="system", request_key="system-suspend"),
                now=102,
            )

        self.assertEqual(self_rejection.status, 403)
        self.assertEqual(self_rejection.payload["code"], "self_lockout")
        self.assertEqual(system_rejection.status, 403)
        self.assertEqual(system_rejection.payload["code"], "system_account")

    def test_combined_user_profile_manage_rejects_invalid_status(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(github_user_id,login_snapshot,status,created_at,updated_at) "
                    "VALUES('456','alice','active',2,2)"
                )
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            token = exchanged.payload["token"]
            confirmation = api.dispatch(
                "confirm",
                "system.users",
                data={"user_id": "456"},
                headers=self.headers(token, team_id="system"),
                now=101,
            )
            confirmation_token = confirmation.payload["confirmation_token"]
            response = api.dispatch(
                "manage",
                "system.users",
                data={
                    "user_id": "456",
                    "status": "archived",
                    "confirmation_token": confirmation_token,
                },
                headers=self.headers(token, team_id="system", request_key="profile-bad"),
                now=102,
            )
        self.assertEqual(response.status, 400)
        self.assertEqual(response.payload["code"], "invalid_request")

    def test_permission_assignments_filter_and_paginate_on_server(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(github_user_id,login_snapshot,created_at,updated_at) "
                    "VALUES('456','target',1,1)"
                )
            grant = api.authorization.create_grant(
                github_user_id="456",
                team_id="template",
                permissions=frozenset({
                    "team.template.packages.read",
                    "team.template.packages.manage",
                }),
                expires_at=None,
                created_by="123",
                now=2,
            )
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            result = api.dispatch(
                "read",
                "team.template.permission_assignments",
                data={
                    "user_id": "456",
                    "node_like": "packages.manage",
                    "limit": 1,
                    "offset": 0,
                },
                headers=self.headers(exchanged.payload["token"], request_key="assignment-read"),
                now=101,
            )

        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["total"], 1)
        self.assertEqual(len(result.payload["assignments"]), 1)
        self.assertEqual(result.payload["assignments"][0]["grant_id"], grant.grant_id)

    def test_team_audit_search_and_export_require_team_read(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO audit_events(actor, action, target, metadata_json, created_at, team_id) "
                    "VALUES('github:123','package.publish','example.mod@1.0.0','{}',2,'system')"
                )
                connection.execute(
                    "INSERT INTO audit_events(actor, action, target, metadata_json, created_at, team_id) "
                    "VALUES('github:456','email.failed','msg-1','{}',3,'default')"
                )
            exchanged = api.dispatch(
                "exchange",
                "system.authentication.github",
                body=b'{"access_token":"token"}',
                now=100,
            )
            token = exchanged.payload["token"]
            read = api.dispatch(
                "read",
                "team.system.audit",
                data={"query": "package.publish", "limit": 10, "offset": 0},
                headers=self.headers(token, team_id="system"),
                now=101,
            )
            exported = api.dispatch(
                "export",
                "team.system.audit",
                data={},
                headers=self.headers(token, team_id="system"),
                now=102,
            )

        self.assertEqual(read.status, 200)
        self.assertEqual(read.payload["total"], 1)
        self.assertEqual(read.payload["events"][0]["action"], "package.publish")
        self.assertEqual(read.payload["workspace_kind"], "system")
        self.assertEqual(exported.status, 200)
        self.assertTrue(exported.payload["csv"].startswith("created_at,actor,action,target"))

    def test_user_system_permissions_return_all_stored_nodes(self) -> None:
        """用户系统级节点读取不做前缀过滤：编辑器展示完整目录，读回必须是同一集合，
        否则"能勾但不回显"的节点会在下次保存时被静默丢掉。"""
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO users(github_user_id,login_snapshot,created_at,updated_at) "
                    "VALUES('456','member',2,2)"
                )
                upsert_assignment_grant(
                    connection,
                    grant_id="direct-system:456",
                    user_id="456",
                    team_id=None,
                    nodes=(
                        "system.users.read",
                        "team.system.permission_templates.manage",
                        "team.alpha.packages.read",
                    ),
                    source_type="direct",
                    source_id="123",
                    created_by="123",
                    now=2,
                )
            token = api.authentication.sessions.create("123", ttl=1000, now=100)["token"]
            read = api.dispatch(
                "read",
                "system.users.permissions",
                data={"user_id": "456"},
                headers=self.headers(token, team_id="system", request_key="system-nodes"),
                now=101,
            )

        self.assertEqual(read.status, 200)
        values = sorted(item["value"] for item in read.payload["permissions"])
        self.assertEqual(values, [
            "system.users.read",
            "team.alpha.packages.read",
            "team.system.permission_templates.manage",
        ])

    def test_assignment_deny_blocks_and_reports_effect_change(self) -> None:
        """`deny` 就是"阻止"：同一节点上 deny 与 allow 同优先级时判为拒绝；
        preview 要报出真实的 `effect_changed`。"""
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            api.key_issuer.database.claim_first_owner("123", now=1)
            owner = api.authentication.sessions.create("123", ttl=1000, now=100)["token"]
            api.authentication.sessions.create("456", ttl=1000, now=100)
            node = "system.users.read"
            created = api.dispatch(
                "manage",
                "team.system.permission_assignments",
                data={"user_id": "456", "permissions": {node: "allow"}, "expires_at": None},
                headers=self.headers(owner, team_id="system", request_key="deny-create"),
                now=101,
            )
            grant_id = created.payload["grant_id"]
            allowed_before = api.authorization_service.allows("456", node, now=101)
            preview = api.dispatch(
                "manage",
                "team.system.permission_assignments",
                data={"grant_id": grant_id, "permissions": {node: "deny"}, "preview": True},
                headers=self.headers(owner, team_id="system", request_key="deny-preview"),
                now=102,
            )
            updated = api.dispatch(
                "manage",
                "team.system.permission_assignments",
                data={"grant_id": grant_id, "permissions": {node: "deny"}},
                headers=self.headers(owner, team_id="system", request_key="deny-update"),
                now=103,
            )
            allowed_after = api.authorization_service.allows("456", node, now=103)
            listed = api.dispatch(
                "read",
                "team.system.permission_assignments",
                data={"limit": 10, "offset": 0},
                headers=self.headers(owner, team_id="system", request_key="deny-list"),
                now=104,
            )

        self.assertEqual(created.status, 200)
        self.assertTrue(allowed_before)
        self.assertIs(preview.payload["diff"]["effect_changed"], True)
        self.assertEqual(updated.status, 200)
        self.assertFalse(allowed_after)
        self.assertEqual(
            [row["effect"] for row in listed.payload["assignments"] if row["node"] == node],
            ["deny"],
        )

    def test_assignment_accepts_template_instance_node(self) -> None:
        """模板实例节点也能走分配路径：目录里有它、`.grant` 解析到它自己，
        授权后模板成员权限生效，列表里显示的就是该节点。"""
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            api.key_issuer.database.claim_first_owner("123", now=1)
            owner = api.authentication.sessions.create("123", ttl=1000, now=100)["token"]
            api.authentication.sessions.create("456", ttl=1000, now=100)
            template_node = "team.system.templates.template-user"
            created = api.dispatch(
                "manage",
                "team.system.permission_assignments",
                data={"user_id": "456", "permissions": {template_node: "allow"}, "expires_at": None},
                headers=self.headers(owner, team_id="system", request_key="assign-template"),
                now=101,
            )
            allowed = api.authorization_service.allows(
                "456", "system.authentication.session.revoke", now=101, team_id="system",
            )
            listed = api.dispatch(
                "read",
                "team.system.permission_assignments",
                data={"limit": 10, "offset": 0},
                headers=self.headers(owner, team_id="system", request_key="assign-list"),
                now=102,
            )

        self.assertEqual(created.status, 200)
        self.assertTrue(allowed)
        self.assertIn(template_node, [row["node"] for row in listed.payload["assignments"]])

    def test_business_action_auto_adds_resource_read(self) -> None:
        """后端不变量：提交动作节点时自动补同资源的 `read` 前置（目录中存在才补）。"""
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            api.key_issuer.database.claim_first_owner("123", now=1)
            owner = api.authentication.sessions.create("123", ttl=1000, now=100)["token"]
            api.authentication.sessions.create("456", ttl=1000, now=100)
            applied = api.dispatch(
                "manage",
                "system.users.permissions",
                data={"user_id": "456", "permissions": {"system.users.manage": "allow"}},
                headers=self.headers(owner, team_id="system", request_key="auto-read"),
                now=101,
            )

        self.assertEqual(applied.status, 200)
        values = sorted(item["value"] for item in applied.payload["permissions"])
        self.assertEqual(values, ["system.users.manage", "system.users.read"])

    def test_template_instance_is_a_catalog_node_that_expands(self) -> None:
        """模板实例是后端权限树里的节点：出现在目录里、可以像节点一样提交，
        求值时展开成模板成员节点，删除后成员权限随之消失。"""
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            owner = api.authentication.sessions.create("123", ttl=1000, now=100)["token"]
            api.authentication.sessions.create("456", ttl=1000, now=100)
            headers = self.headers(owner, team_id="system", request_key="user-nodes")

            catalog = api.dispatch(
                "read",
                "system.schema.permissions",
                data={},
                headers=headers,
                now=100,
            )
            template_node = "team.system.templates.template-user"
            self.assertIn(template_node, catalog.payload["permissions"])
            # 每个模板实例自带 .grant：允许把这个模板分配给其他人。
            self.assertIn(f"{template_node}.grant", catalog.payload["permissions"])

            applied = api.dispatch(
                "manage",
                "system.users.permissions",
                data={"user_id": "456", "permissions": {template_node: "allow"}},
                headers=headers,
                now=101,
            )
            allowed = api.authorization_service.allows("456", "system.authentication.session.revoke", now=101)
            read = api.dispatch(
                "read",
                "system.users.permissions",
                data={"user_id": "456"},
                headers=headers,
                now=102,
            )
            revoked = api.dispatch(
                "manage",
                "system.users.permissions",
                data={"user_id": "456", "permissions": {}},
                headers=self.headers(owner, team_id="system", request_key="user-nodes-clear"),
                now=103,
            )
            after = api.authorization_service.allows("456", "system.authentication.session.revoke", now=103)

        self.assertEqual(applied.status, 200)
        self.assertTrue(allowed)
        self.assertEqual(
            [item["value"] for item in read.payload["permissions"]],
            [template_node],
        )
        self.assertEqual(revoked.status, 200)
        self.assertEqual(revoked.payload["permissions"], [])
        self.assertFalse(after)

    def test_team_invite_requires_invite_node_not_manage(self) -> None:
        """Team 邀请按 `Invite` 节点授权：持有 invite 的 Team Admin 成功，
        只有 `users.read` 的成员被拒绝（team_permission_denied）。"""
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            inviter = api.authentication.sessions.create("456", ttl=1000, now=100)["token"]
            reader = api.authentication.sessions.create("789", ttl=1000, now=100)["token"]
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO teams(team_id,name,description,owner_user_id,created_by,created_at,updated_at) "
                    "VALUES('alpha','Alpha','','123','123',2,2)"
                )
                connection.execute(
                    "INSERT INTO users(github_user_id,login_snapshot,created_at,updated_at) "
                    "VALUES('999','invitee',2,2)"
                )
                connection.execute(
                    "INSERT INTO permission_templates"
                    "(template_id,name,template_kind,team_id,created_by,created_at) "
                    "VALUES('alpha:tester','Tester','permission_template','alpha','123',2)"
                )
                connection.execute(
                    "INSERT INTO template_assignments"
                    "(assignment_id,template_id,node,effect,priority,grant_effect) "
                    "VALUES('alpha:tester:0','alpha:tester','team.alpha.packages.read','allow',0,'allow')"
                )
                upsert_assignment_grant(
                    connection,
                    grant_id="alpha-invite-grant",
                    user_id="456",
                    team_id="alpha",
                    nodes=("team.alpha.users.invite",),
                    source_type="manual",
                    source_id="contract-test",
                    created_by="123",
                    now=2,
                )
                upsert_assignment_grant(
                    connection,
                    grant_id="alpha-read-grant",
                    user_id="789",
                    team_id="alpha",
                    nodes=("team.alpha.users.read",),
                    source_type="manual",
                    source_id="contract-test",
                    created_by="123",
                    now=2,
                )
            inviter = api.authentication.sessions.create("456", ttl=1000, now=100)["token"]
            reader = api.authentication.sessions.create("789", ttl=1000, now=100)["token"]
            invited = api.dispatch(
                "invite",
                "team.alpha.users",
                data={"user_id": "999", "permission_template": "Tester"},
                headers=self.headers(inviter, team_id="alpha", request_key="alpha-invite"),
                now=101,
            )
            denied = api.dispatch(
                "invite",
                "team.alpha.users",
                data={"user_id": "999", "permission_template": "Tester"},
                headers=self.headers(reader, team_id="alpha", request_key="alpha-invite-denied"),
                now=102,
            )

        self.assertEqual(invited.status, 200)
        self.assertEqual(denied.status, 403)
        self.assertEqual(denied.payload["code"], "team_permission_denied")
