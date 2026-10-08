from __future__ import annotations

import hashlib
import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sprocket_access_server.modules.system.authentication import AuthenticationService
from sprocket_access_server.modules.packages.publication import PackagePublisher
from sprocket_access_server.modules.packages.download_tokens import DownloadTokenCodec
from sprocket_access_server.core.ports import GitHubIdentity
from sprocket_access_server.domain.protocol import ServerInfo
from sprocket_access_server.modules.audit.store import SQLiteAuditStore
from sprocket_access_server.modules.permission_assignments.store import SQLiteAuthorizationStore
from sprocket_access_server.infrastructure.utilities.confirmations import SQLiteConfirmationStore
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.events import ResourceChanged
from sprocket_access_server.modules.permission_templates.store import SQLiteGrantTemplateStore
from sprocket_access_server.infrastructure.utilities.idempotency import SQLiteIdempotencyStore
from sprocket_access_server.modules.keys.store import KeyIssuer
from sprocket_access_server.modules.packages.store import SQLitePackageStore, PackageVersion
from sprocket_access_server.infrastructure.permissions import PermissionCatalog
from sprocket_access_server.modules.system.platform import PlatformStore
from sprocket_access_server.infrastructure.security.sessions import SQLiteSessionStore
from sprocket_access_server.infrastructure.storage.objects import LocalFileObjectStorage
from sprocket_access_server.infrastructure.storage.uploads import SQLiteUploadStore
from sprocket_access_server.modules.system.provisioning import SystemProvisioning
from tests.support.resource_harness import ResourceDispatchHarness

# 包必须声明安装规则，版本行记录同一份快照；直接调 store.publish 的用例用它当元数据。
PACKAGE_METADATA = {"install": {"files": [{"match": "*.dll", "type": "melonloader:mod"}],
                                "scan_dlls": True, "exclude": []}}


def archive_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


class FakeGitHub:
    def verify_access_token(self, _token: str) -> GitHubIdentity:
        return GitHubIdentity("123", "user")


class DynamicGitHub:
    def __init__(self, user_id: str):
        self.user_id = user_id

    def verify_access_token(self, _token: str) -> GitHubIdentity:
        return GitHubIdentity(self.user_id, "user")


class HttpApiTests(unittest.TestCase):
    def api(self, directory: str) -> ResourceDispatchHarness:
        database = SQLiteDatabase(Path(directory) / "access.db")
        sessions = SQLiteSessionStore(database, b"pepper")
        platform = PlatformStore(database, "simple")
        provisioning = SystemProvisioning(database, "simple")
        auth = AuthenticationService(FakeGitHub(), sessions, session_ttl=100,
                                     ownership=database, provisioning=provisioning)
        packages = SQLitePackageStore(database)
        api = ResourceDispatchHarness(
            ServerInfo("server-1", "Test", None, {}), auth,
            key_issuer=KeyIssuer(database, SQLiteAuthorizationStore(database), b"key-pepper"),
            idempotency=SQLiteIdempotencyStore(database),
            authorization=SQLiteAuthorizationStore(database),
            packages=SQLitePackageStore(database),
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

        dispatch = api.dispatch

        def dispatch_with_default_team(action: str, node: str, **kwargs):
            headers = dict(kwargs.pop("headers", {}) or {})
            if "Authorization" in headers and "X-Team-Id" not in headers:
                headers["X-Team-Id"] = "default"
            return dispatch(action, node, headers=headers, **kwargs)

        api.dispatch = dispatch_with_default_team

        # Default direct fixture writes to the "default" team. Explicit
        # team_id values remain untouched.
        create_batch = api.key_issuer.create_batch
        api.key_issuer.create_batch = lambda *args, **kwargs: create_batch(
            *args, team_id=kwargs.pop("team_id", "default"), **kwargs)
        redeem_key = api.authorization.redeem_key
        api.authorization.redeem_key = lambda *args, **kwargs: redeem_key(
            *args, team_id=kwargs.pop("team_id", "default"), **kwargs)
        publish = api.packages.publish
        api.packages.publish = lambda *args, **kwargs: publish(
            *args, team_id=kwargs.pop("team_id", "default"), **kwargs)
        create_template = api.permission_templates.create
        api.permission_templates.create = lambda *args, **kwargs: create_template(
            *args, team_id=kwargs.pop("team_id", "default"), **kwargs)
        record_audit = api.audit.record
        api.audit.record = lambda *args, **kwargs: record_audit(
            *args, team_id=kwargs.pop("team_id", "default"), **kwargs)
        return api

    def test_server_info_and_structured_not_found(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            info = api.dispatch("read", "system.server_info")
            missing = api.dispatch("read", "system.missing")
        self.assertEqual(info.status, 200)
        self.assertEqual(info.payload["protocol_version"], 2)
        self.assertEqual(missing.status, 404)
        self.assertEqual(missing.payload["code"], "not_found")

    def test_overview_uses_team_operational_counts(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            api.platform = PlatformStore(database, "simple")
            api.provisioning.ensure_user("123", "owner", now=2)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=90)
            api.module_context.events.publish(ResourceChanged(
                kind="create", node="team.default", action="manage", data={}, team_id="default"))
            issued = api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.laser.download"}),
                                                 expires_at=150, actor="123", team_id="default", now=90)[0]
            api.authorization.redeem_key(key_hash=api.key_issuer.hash_key(issued.plaintext), github_user_id="123",
                                         team_id="default", now=100)
            api.packages.publish(PackageVersion("default.laser", "1.0.0", "team.default.laser", "a" * 64, 1,
                                                "published", 100, PACKAGE_METADATA), team_id="default", now=100)
            api.upload_store.create(package_id="default.pending", version="1.0.0", size=1,
                                    content_type="application/zip", ttl=10, team_id="default", now=100)
            result = api.dispatch("read", "team.default.overview",
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                           "X-Team-Id": "default"}, now=180)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["workspace_kind"], "team")
        self.assertEqual(result.payload["team_id"], "default")
        self.assertEqual(result.payload["keys"]["total"], 1)
        self.assertEqual(result.payload["grants"], {"total": 2, "expired": 1})
        self.assertEqual(result.payload["packages"], 1)
        self.assertEqual(result.payload["uploads"], {"pending": 0, "timed_out": 1})

    def test_auth_me_returns_permission_assignments(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            api.platform = PlatformStore(database, "simple")
            api.provisioning.ensure_user("123", "owner", now=1)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=2)
            api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.laser.download"}), expires_at=None,
                                        actor="123", team_id="default", now=2)
            result = api.dispatch("read", "system.authentication.me", headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                                                   "X-Team-Id": "default"}, now=3)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["current_team"]["team_id"], "default")
        self.assertEqual(result.payload["permissions"], ["team.default.*"])
        self.assertIn("effective_permissions", result.payload)
        self.assertIn("grantable_permissions", result.payload)

    def test_authorization_context_exposes_effective_and_grantable_assignments(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            database = api.key_issuer.database
            database.claim_first_owner("123", now=1)
            api.platform = PlatformStore(database, "simple")
            api.provisioning.ensure_user("123", "owner", now=1)
            exchanged = api.dispatch("exchange", "system.authentication.github",
                                     body=b'{"access_token":"token"}', now=2)
            result = api.dispatch("read", "system.authentication.me.authorization",
                headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                         "X-Team-Id": "default"}, now=3)
        self.assertEqual(result.status, 200)
        self.assertIn("effective_permissions", result.payload)
        self.assertIn("grantable_permissions", result.payload)
        self.assertIn("permission_assignments", result.payload)
        self.assertTrue(result.payload["permission_assignments"])

    def test_admin_key_batch_can_use_permission_template(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            template = api.permission_templates.create(
                name="Laser testers", permissions=frozenset({"team.default.packages.laser.download"}),
                expires_in=3600, created_by="123", now=100,
            )
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            result = api.dispatch("distribute", "team.default.keys",
                body=json.dumps({"quantity": 1, "template_id": template.template_id}).encode(),
                headers={"Authorization": f"Bearer {exchanged.payload['token']}", "Idempotency-Key": "template-batch"},
                now=100,
            )
        self.assertEqual(result.status, 201)
        self.assertEqual(result.payload["keys"][0]["expires_at"], 3700)

    def test_template_key_persists_assignments_through_redemption(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            template = api.permission_templates.create(
                name="Laser assignments", permissions=frozenset({"team.default.packages.laser.download"}),
                expires_in=None, created_by="123", now=100,
            )
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            issued = api.dispatch("distribute", "team.default.keys",
                body=json.dumps({"quantity": 1, "template_id": template.template_id}).encode(),
                headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                         "Idempotency-Key": "template-assignment-batch"},
                now=100,
            )
            key = issued.payload["keys"][0]
            with api.key_issuer.database.transaction() as connection:
                stored_key = connection.execute(
                    "SELECT template_id,key_kind,assignments_json FROM activation_keys WHERE key_id=?",
                    (key["key_id"],),
                ).fetchone()
            redeemed = api.dispatch("distribute", "system.keys.redeem",
                body=json.dumps({"key": key["key"]}).encode(),
                headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                         "Idempotency-Key": "template-assignment-redeem"},
                now=101,
            )
            with api.key_issuer.database.transaction() as connection:
                stored_assignment = connection.execute(
                    "SELECT node,effect,priority FROM grant_assignments WHERE grant_id=?",
                    (redeemed.payload["grant"]["grant_id"],),
                ).fetchone()
        self.assertEqual(issued.status, 201)
        self.assertEqual(stored_key["template_id"], template.template_id)
        self.assertEqual(stored_key["key_kind"], "template")
        self.assertEqual(json.loads(stored_key["assignments_json"])[0]["node"], "team.default.packages.laser.download")
        self.assertEqual(redeemed.status, 200)
        self.assertEqual(tuple(stored_assignment), ("team.default.packages.laser.download", "allow", 0))

    def test_permission_catalog_combines_system_package_template_and_custom_entries(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            api.packages.publish(PackageVersion("default.laser.mod", "1.0.0", "team.default.laser_mod", "a" * 64, 1,
                                                "published", 100, PACKAGE_METADATA), now=100)
            api.permission_templates.create(name="QA", permissions=frozenset({"team.default.laser_mod.read"}), expires_in=None,
                                       created_by="123", now=100)
            api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.custom.beta.read"}), expires_at=None,
                                        actor="123", now=100)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            result = api.dispatch("read", "system.permissions",
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=101)
        entries = {item["value"]: item["source"] for item in result.payload["permissions"]}
        self.assertEqual(result.status, 200)
        self.assertEqual(entries["team.default.laser_mod.download"], "stored")
        self.assertEqual(entries["team.default.laser_mod.read"], "template")
        self.assertEqual(entries["team.default.custom.beta.read"], "stored")
        self.assertIn("system.*", entries)
        self.assertEqual(result.payload["templates"][0]["name"], "QA")

    def test_key_mutations_reject_invalid_permission_namespace(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}", "Idempotency-Key": "invalid-perm"}
            created = api.dispatch("distribute", "team.default.keys",
                                   body=b'{"quantity":1,"permissions":["bad namespace:value"]}', headers=headers,
                                   now=100)
            issued = api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.laser.download"}),
                                                 expires_at=None, actor="123", now=100)[0]
            updated = api.dispatch("manage", "team.default.keys", data={"key_id": issued.key_id},
                                   body=b'{"permissions":["unknown:value"]}',
                                   headers={**headers, "Idempotency-Key": "invalid-update"}, now=101)
        self.assertEqual((created.status, updated.status), (400, 400))

    def test_permission_template_lifecycle_is_idempotent_and_disable_requires_confirmation(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            api.module_context.events.publish(ResourceChanged(
                kind="create", node="team.default", action="manage", data={}, team_id="default"))
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}",
                       "Idempotency-Key": "template-create"}
            body = b'{"name":"QA","permissions":["team.default.packages.read"],"expires_in":3600}'
            created = api.dispatch("create", "team.default.permission_templates", body=body, headers=headers, now=100)
            replay = api.dispatch("create", "team.default.permission_templates", body=body, headers=headers, now=101)
            template_id = created.payload["template_id"]
            denied = api.dispatch("manage", "team.default.permission_templates", data={"template_id": template_id},
                                  body=b'{"status":"disabled"}',
                                  headers={**headers, "Idempotency-Key": "template-disable-denied"}, now=102)
            confirmation = api.dispatch("confirm", "team.default.permission_templates", data={"template_id": template_id},
                                        headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=103)
            disabled = api.dispatch("manage", "team.default.permission_templates", data={"template_id": template_id},
                                    body=b'{"status":"disabled"}',
                                    headers={**headers, "Idempotency-Key": "template-disable",
                                             "X-Confirmation-Token": confirmation.payload["confirmation_token"]},
                                    now=104)
            activated = api.dispatch("manage", "team.default.permission_templates", data={"template_id": template_id},
                                     body=b'{"status":"active","name":"QA 2","permissions":["team.default.packages.read","team.default.packages.manage"],"expires_in":null}',
                                     headers={**headers, "Idempotency-Key": "template-activate"}, now=105)
            listed = api.dispatch("read", "team.default.permission_templates",
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=106)
        self.assertEqual(created.payload, replay.payload)
        self.assertEqual(denied.payload["code"], "confirmation_required")
        self.assertEqual(disabled.payload["status"], "disabled")
        self.assertEqual(activated.payload["status"], "active")
        self.assertEqual(activated.payload["name"], "QA 2")
        self.assertEqual(len(listed.payload["templates"]), 1)

    def test_permission_template_api_rejects_invalid_permissions(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            created = api.dispatch("create", "team.default.permission_templates",
                                   body=b'{"name":"Bad","permissions":["unknown:value"]}',
                                   headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                            "Idempotency-Key": "template-invalid"}, now=100)
        self.assertEqual(created.status, 400)

    def test_admin_can_search_keys_without_exposing_plaintext(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            issued = api.dispatch("distribute", "team.default.keys", body=b'{"quantity":2,"permissions":["team.default.packages.laser.download"]}',
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                           "Idempotency-Key": "search-batch"}, now=100)
            result = api.dispatch("read", "team.default.keys",
                                  data={"status": "unused", "limit": 1},
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=101)
        self.assertEqual(issued.status, 201)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["counts"]["unused"], 2)
        self.assertEqual(len(result.payload["keys"]), 1)
        self.assertNotIn("key", result.payload["keys"][0])

    def test_key_update_addition_is_idempotent_without_confirmation(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            issued = api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.laser.download"}),
                                                 expires_at=500, actor="123", now=100)[0]
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}",
                       "Idempotency-Key": "key-add-permission"}
            body = b'{"note":"beta","permissions":["team.default.packages.laser.download","team.default.packages.laser.read"],"expires_at":600}'
            first = api.dispatch("manage", "team.default.keys", data={"key_id": issued.key_id}, body=body, headers=headers, now=101)
            replay = api.dispatch("manage", "team.default.keys", data={"key_id": issued.key_id}, body=body, headers=headers, now=102)
        self.assertEqual(first.status, 200)
        self.assertEqual(first.payload, replay.payload)
        self.assertFalse(first.payload["requires_confirmation"])
        self.assertEqual(first.payload["updated"]["note"], "beta")

    def test_redeemed_key_downgrade_requires_confirmation_and_syncs_grant(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            issued = api.key_issuer.create_batch(quantity=1,
                                                 permissions=frozenset({"team.default.packages.laser.download", "team.default.packages.laser.read"}),
                                                 expires_at=500, actor="123", now=100)[0]
            redeemed = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                                    headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                             "Idempotency-Key": "key-edit-redeem"}, now=101)
            body = b'{"permissions":["team.default.packages.laser.download"],"expires_at":400}'
            denied = api.dispatch("manage", "team.default.keys", data={"key_id": issued.key_id}, body=body,
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                           "Idempotency-Key": "key-edit-denied"}, now=102)
            confirmation = api.dispatch("confirm", "team.default.keys", data={"key_id": issued.key_id},
                                        headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=103)
            changed = api.dispatch("manage", "team.default.keys", data={"key_id": issued.key_id}, body=body,
                                   headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                            "Idempotency-Key": "key-edit-confirmed",
                                            "X-Confirmation-Token": confirmation.payload["confirmation_token"]},
                                   now=104)
            grant = next(
                item for item in api.authorization.grants("123")
                if item.grant_id == redeemed.payload["grant"]["grant_id"]
            )
        self.assertEqual(denied.status, 400)
        self.assertEqual(denied.payload["code"], "confirmation_required")
        self.assertEqual(changed.status, 200)
        self.assertEqual(changed.payload["grant"]["grant_id"], redeemed.payload["grant"]["grant_id"])
        self.assertEqual(grant.permissions, frozenset({"team.default.packages.laser.download"}))
        self.assertEqual(grant.expires_at, 400)

    def test_key_revoke_requires_confirmation_and_revokes_source_grant(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            issued = api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.laser.download"}),
                                                 expires_at=None, actor="123", now=100)[0]
            api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                         headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                  "Idempotency-Key": "key-revoke-redeem"}, now=101)
            confirmation = api.dispatch("confirm", "team.default.keys", data={"key_id": issued.key_id},
                                        headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=102)
            revoked = api.dispatch("manage", "team.default.keys", data={"key_id": issued.key_id}, body=b'{"status":"revoked"}',
                                   headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                            "Idempotency-Key": "key-revoke",
                                            "X-Confirmation-Token": confirmation.payload["confirmation_token"]},
                                   now=103)
        self.assertEqual(revoked.status, 200)
        self.assertEqual(revoked.payload["updated"]["status"], "revoked")
        self.assertEqual(revoked.payload["grant"]["status"], "revoked")

    def test_admin_can_search_grants_by_user_and_status(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            api.packages.publish(PackageVersion("default.laser", "1.0.0", "team.default.laser", "a" * 64, 1,
                                                "published", 100, PACKAGE_METADATA), now=100)
            issued = api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.laser.download"}), expires_at=None,
                                                 actor="123", now=100)[0]
            api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                         headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                  "Idempotency-Key": "grant-search"}, now=100)
            result = api.dispatch("read", "team.default.permission_assignments",
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=101)
        self.assertEqual(result.status, 200)
        self.assertEqual(len(result.payload["assignments"]), 1)
        download_assignments = [
            item for item in result.payload["assignments"]
            if item["node"] == "team.default.laser.download"
        ]
        self.assertEqual(len(download_assignments), 1)

    def test_grant_edit_requires_confirmation_for_permission_removal_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            api.module_context.events.publish(ResourceChanged(
                kind="create", node="team.default", action="manage", data={}, team_id="default"))
            api.packages.publish(PackageVersion("default.laser", "1.0.0", "team.default.laser", "a" * 64, 1,
                                                "published", 100, PACKAGE_METADATA), now=100)
            api.module_context.events.publish(ResourceChanged(
                kind="create", node="team.default.laser", action="manage",
                data={"package_id": "default.laser"}, team_id="default"))
            issued = api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.laser.download", "team.default.laser.read"}),
                                                 expires_at=500, actor="123", now=100)[0]
            redeemed = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                                    headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                             "Idempotency-Key": "grant-edit-redeem"}, now=101)
            grant_id = redeemed.payload["grant"]["grant_id"]
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}", "Idempotency-Key": "grant-edit"}
            body = b'{"permissions":{"team.default.laser.read":"allow"}}'
            rejected = api.dispatch("manage", "team.default.permission_assignments", data={"assignment_id": grant_id},
                                    body=body, headers=headers, now=110)
            confirmation = api.dispatch("edit_confirmation", "team.default.permission_assignments", data={"assignment_id": grant_id},
                                        headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=110)
            accepted = api.dispatch("manage", "team.default.permission_assignments", data={"assignment_id": grant_id},
                                    body=body,
                                    headers={**headers, "X-Confirmation-Token": confirmation.payload["confirmation_token"]},
                                    now=110)
            replay = api.dispatch("manage", "team.default.permission_assignments", data={"assignment_id": grant_id},
                                  body=body, headers=headers, now=111)
        self.assertEqual(rejected.status, 400)
        self.assertEqual(rejected.payload["code"], "confirmation_required")
        self.assertEqual(accepted.status, 200)
        self.assertEqual(accepted.payload["diff"]["permissions_removed"], ["team.default.laser.download"])
        self.assertEqual(replay.status, 200)
        self.assertEqual(replay.payload["grant_id"], grant_id)

    def test_grant_revoke_requires_one_time_confirmation(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            issued = api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.laser.download"}), expires_at=None,
                                                 actor="123", now=100)[0]
            redeemed = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                                    headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                             "Idempotency-Key": "revoke-redeem"}, now=100)
            grant_id = redeemed.payload["grant"]["grant_id"]
            confirmation = api.dispatch("confirm", "team.default.permission_assignments",
                                        data={"assignment_id": grant_id},
                                        headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=100)
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}",
                       "X-Confirmation-Token": confirmation.payload["confirmation_token"],
                       "Idempotency-Key": "revoke-grant"}
            revoked = api.dispatch("revoke", "team.default.permission_assignments", data={"assignment_id": grant_id}, headers=headers, now=101)
            replay = api.dispatch("revoke", "team.default.permission_assignments", data={"assignment_id": grant_id},
                                  headers={**headers, "Idempotency-Key": "revoke-grant-2"}, now=102)
        self.assertEqual(revoked.status, 200)
        self.assertEqual(replay.status, 400)

    def test_grant_suspend_uses_separate_confirmation_action(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            issued = api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.laser.download"}), expires_at=None,
                                                 actor="123", now=100)[0]
            redeemed = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                                    headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                             "Idempotency-Key": "suspend-redeem"}, now=100)
            grant_id = redeemed.payload["grant"]["grant_id"]
            confirmation = api.dispatch("confirm", "team.default.permission_assignments",
                                        data={"assignment_id": grant_id, "mode": "suspend"},
                                        headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=100)
            suspended = api.dispatch("suspend", "team.default.permission_assignments", data={"assignment_id": grant_id},
                                     headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                              "X-Confirmation-Token": confirmation.payload["confirmation_token"],
                                              "Idempotency-Key": "suspend-grant"}, now=101)
        self.assertEqual(suspended.status, 200)
        self.assertEqual(suspended.payload["status"], "suspended")

    def test_admin_can_extend_active_grant_only_forward(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            issued = api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.laser.download"}), expires_at=500,
                                                 actor="123", now=100)[0]
            redeemed = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                                    headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                             "Idempotency-Key": "extend-redeem"}, now=100)
            grant_id = redeemed.payload["grant"]["grant_id"]
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}", "Idempotency-Key": "extend-grant"}
            extended = api.dispatch("extend", "team.default.permission_assignments", data={"assignment_id": grant_id}, body=b'{"expires_at":700}',
                                    headers=headers, now=101)
            rejected = api.dispatch("extend", "team.default.permission_assignments", data={"assignment_id": grant_id}, body=b'{"expires_at":600}',
                                    headers={**headers, "Idempotency-Key": "extend-grant-2"}, now=102)
        self.assertEqual(extended.status, 200)
        self.assertEqual(extended.payload["expires_at"], 700)
        self.assertEqual(rejected.status, 404)

    def test_admin_can_search_private_package_versions(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            api.packages.publish(PackageVersion("default.laser", "1.0.0", "team.default.packages.laser", "a" * 64, 12, "published", 100, PACKAGE_METADATA),
                                 now=100)
            result = api.dispatch("read", "team.default.packages",
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=101)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["packages"][0]["package_id"], "default.laser")
        self.assertEqual(result.payload["packages"][0]["archive_digest"], "a" * 64)

    def test_package_status_change_requires_confirmation_and_preserves_version(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            api.packages.publish(PackageVersion("default.laser", "1.0.0", "team.default.packages.laser", "a" * 64, 12, "published", 100, PACKAGE_METADATA),
                                 now=100)
            confirmation = api.dispatch("confirm", "team.default.packages", data={"package_id": "default.laser", "version": "1.0.0"},
                                        headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=100)
            changed = api.dispatch("status", "team.default.packages", data={"package_id": "default.laser", "version": "1.0.0"}, body=b'{"status":"disabled"}',
                                   headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                            "X-Confirmation-Token": confirmation.payload["confirmation_token"],
                                            "Idempotency-Key": "disable-package"}, now=101)
            package = api.packages.get("default.laser", "1.0.0")
        self.assertEqual(changed.status, 200)
        self.assertEqual(changed.payload["status"], "disabled")
        self.assertEqual(package.archive_digest, "a" * 64)

    def test_admin_can_update_package_metadata_idempotently(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            api.packages.publish(PackageVersion("default.laser", "1.0.0", "team.default.packages.laser", "a" * 64, 12, "published", 100, PACKAGE_METADATA),
                                 now=100)
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}", "Idempotency-Key": "metadata-1"}
            # 版本元数据是整体替换：提交里必须带可发布的完整文档（安装规则）。
            body = json.dumps({"metadata": {**PACKAGE_METADATA, "channel": "beta",
                                            "display_name": {"en": "Laser Beta"}}}).encode("utf-8")
            changed = api.dispatch("metadata", "team.default.packages", data={"package_id": "default.laser", "version": "1.0.0"},
                                   body=body,
                                   headers=headers, now=101)
            replay = api.dispatch("metadata", "team.default.packages", data={"package_id": "default.laser", "version": "1.0.0"},
                                  body=body,
                                  headers=headers, now=102)
            stored = api.packages.get("default.laser", "1.0.0")
        self.assertEqual(changed.status, 200)
        self.assertEqual(changed.payload, replay.payload)
        self.assertEqual(stored.metadata["channel"], "beta")

    def test_admin_can_search_redacted_audit_events(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            api.audit.record(actor="github:123", action="test.secret", target="x", metadata={"token": "hidden"},
                             now=100)
            result = api.dispatch("read", "team.default.audit",
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=101)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["events"][0]["metadata"]["token"], "[REDACTED]")

    def test_admin_can_export_redacted_audit_csv(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            api.audit.record(actor="github:123", action="test.export", target="x", metadata={"secret": "hidden"},
                             now=100)
            result = api.dispatch("export", "team.default.audit",
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=101)
        self.assertEqual(result.status, 200)
        self.assertTrue(result.headers["Content-Type"].startswith("text/csv"))
        self.assertIn("created_at,actor,action,target,metadata_json", result.payload)
        self.assertIn("[REDACTED]", result.payload)

    def test_exchange_then_entitlements_requires_bearer_session(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github",
                body=json.dumps({"access_token": "token"}).encode(), now=100,
            )
            token = exchanged.payload["token"]
            entitlements = api.dispatch("read", "system.entitlements",
                headers={"Authorization": f"Bearer {token}"}, now=101,
            )
        self.assertEqual(exchanged.status, 200)
        self.assertEqual(entitlements.status, 200)
        self.assertEqual(entitlements.payload["github_user_id"], "123")

    def test_admin_key_batch_requires_admin_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github",
                body=json.dumps({"access_token": "token"}).encode(), now=100,
            )
            headers = {
                "Authorization": f"Bearer {exchanged.payload['token']}",
                "Idempotency-Key": "batch-1",
            }
            body = json.dumps({"quantity": 2, "permissions": ["team.default.packages.mod.download"]}).encode()
            first = api.dispatch("distribute", "team.default.keys", body=body, headers=headers, now=101)
            second = api.dispatch("distribute", "team.default.keys", body=body, headers=headers, now=102)
            with api.key_issuer.database.transaction() as connection:
                stored = connection.execute("SELECT response_json FROM idempotency_records").fetchall()
            events = api.audit.recent()
        self.assertEqual(first.status, 201)
        self.assertEqual(first.headers["Cache-Control"], "no-store")
        self.assertEqual(first.payload, second.payload)
        self.assertEqual(len(first.payload["keys"]), 2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "key_batch.create")
        self.assertNotIn("keys", events[0]["metadata"])
        self.assertEqual(stored, [])

    def test_key_batch_plaintext_retry_cache_expires(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            api._VOLATILE_KEY_BATCH_TTL = 40
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}", "Idempotency-Key": "expiring-batch"}
            body = b'{"quantity":1,"permissions":["team.default.packages.mod.download"]}'
            first = api.dispatch("distribute", "team.default.keys", body=body, headers=headers, now=100)
            expired = api.dispatch("distribute", "team.default.keys", body=body, headers=headers, now=141)
        self.assertNotEqual(first.payload["batch_id"], expired.payload["batch_id"])

    def test_admin_can_read_owned_key_batch_stats(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            issued = api.dispatch("distribute", "team.default.keys", body=b'{"quantity":2,"permissions":["team.default.packages.mod.download"]}',
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                           "Idempotency-Key": "stats-batch"}, now=100)
            batch_id = issued.payload["batch_id"]
            stats = api.dispatch("read_batch", "team.default.keys", data={"batch_id": batch_id},
                                 headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=101)
        self.assertEqual(stats.status, 200)
        self.assertEqual(stats.payload["counts"]["unused"], 2)

    def test_owner_can_read_key_batch_stats_created_by_another_actor(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            issued = api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.mod.download"}), expires_at=None,
                                                 actor="other-admin", now=100)
            stats = api.dispatch("read_batch", "team.default.keys", data={"batch_id": issued[0].batch_id},
                                 headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=101)
        self.assertEqual(stats.status, 200)
        self.assertEqual(stats.payload["created_by"], "other-admin")

    def test_bearer_headers_are_case_insensitive(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github",
                body=json.dumps({"access_token": "token"}).encode(), now=100,
            )
            result = api.dispatch("read", "system.entitlements",
                headers={"authorization": f"Bearer {exchanged.payload['token']}"}, now=101,
            )
        self.assertEqual(result.status, 200)

    def test_redeem_key_requires_session_identity(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            issued = \
            api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.example.download"}), expires_at=None,
                                        actor="admin", now=90)[0]
            result = api.dispatch("distribute", "system.keys.redeem",
                                  body=json.dumps({"key": issued.plaintext, "github_user_id": "123"}).encode(),
                                  headers={"Idempotency-Key": "redeem-1"}, now=100)
        self.assertEqual(result.status, 401)

    def test_redeem_key_retry_reuses_uid_but_new_activation_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            issued = \
            api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.example.download"}), expires_at=None,
                                        actor="admin", now=90)[0]
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=99)
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}", "Idempotency-Key": "redeem-1"}
            first = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                                 headers=headers, now=100)
            second = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                                  headers=headers, now=101)
            third = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                                 headers={**headers, "Idempotency-Key": "redeem-2"}, now=102)
            with api.key_issuer.database.transaction() as connection:
                records = connection.execute("SELECT request_hash, response_json FROM idempotency_records").fetchall()
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertEqual(second.payload, first.payload)
        self.assertEqual(third.status, 409)
        self.assertEqual(third.payload["code"], "key_unavailable")
        self.assertTrue(records)
        self.assertTrue(all(issued.plaintext not in (row["request_hash"] + row["response_json"]) for row in records))

    def test_redeemed_key_is_rejected_for_another_user(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            issued = api.key_issuer.create_batch(
                quantity=1,
                permissions=frozenset({"team.default.packages.test_private-mod_claimed.download"}),
                expires_at=None,
                actor="admin:123",
                now=90,
            )[0]
            api.authentication.github = DynamicGitHub("111")
            first = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"a"}', now=99)
            first_redeem = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                headers={"Authorization": f"Bearer {first.payload['token']}", "Idempotency-Key": "claimed-a"}, now=100,
            )
            api.authentication.github = DynamicGitHub("222")
            second = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"b"}', now=101)
            second_redeem = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                headers={"Authorization": f"Bearer {second.payload['token']}", "Idempotency-Key": "claimed-b"}, now=102,
            )
            self.assertEqual(first_redeem.status, 200)
            self.assertEqual(second_redeem.status, 409)
            self.assertEqual(second_redeem.payload["code"], "key_unavailable")
            with api.key_issuer.database.transaction() as connection:
                row = connection.execute(
                    "SELECT status, redeemed_github_user_id FROM activation_keys WHERE key_id = ?", (issued.key_id,)
                ).fetchone()
            self.assertEqual((row["status"], row["redeemed_github_user_id"]), ("redeemed", "111"))

    def test_session_revoke_invalidates_bearer_token(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=100)
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}"}
            revoked = api.dispatch("revoke", "system.authentication.session", headers=headers, now=101)
            entitlements = api.dispatch("read", "system.entitlements", headers=headers, now=102)
        self.assertEqual(revoked.status, 200)
        self.assertEqual(entitlements.status, 401)

    def test_download_url_rejects_unknown_published_version(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github",
                                     body=json.dumps({"access_token": "token"}).encode(), now=100)
            token = exchanged.payload["token"]
            denied = api.dispatch("download", "team.default.packages", data={"package_id": "default.example"}, body=b'{"version":"1.0.0"}',
                                  headers={"Authorization": f"Bearer {token}"}, now=101)
        self.assertEqual(denied.status, 404)

    def test_download_url_applies_assignment_deny(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            exchanged = api.dispatch("exchange", "system.authentication.github",
                                     body=json.dumps({"access_token": "token"}).encode(), now=100)
            token = exchanged.payload["token"]
            issued = api.key_issuer.create_batch(
                quantity=1,
                permissions=frozenset({"team.default.example.download"}),
                expires_at=None,
                actor="123",
                now=100,
            )[0]
            redeemed = api.dispatch("distribute", "system.keys.redeem",
                body=json.dumps({"key": issued.plaintext}).encode(),
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "download-redeem"},
                now=101,
            )
            self.assertEqual(redeemed.status, 200)
            api.packages.publish(
                PackageVersion("default.example", "1.0.0", "team.default.example", "a" * 64, 1,
                               "published", 100, PACKAGE_METADATA),
                now=100,
            )
            allowed = api.dispatch("download", "team.default.packages", data={"package_id": "default.example"},
                body=b'{"version":"1.0.0"}',
                headers={"Authorization": f"Bearer {token}"},
                now=102,
            )
            grant = next(item for item in api.authorization.grants("123", team_id="default")
                         if item.permissions == frozenset({"team.default.example.download"}))
            with api.key_issuer.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO grant_assignments
                       (assignment_id,grant_id,node,effect,priority,grant_effect,source_type,source_id)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    ("download-deny", grant.grant_id, "team.default.example.download", "deny", 10,
                     "allow", "test", "download-deny"),
                )
            denied = api.dispatch("download", "team.default.packages", data={"package_id": "default.example"},
                body=b'{"version":"1.0.0"}',
                headers={"Authorization": f"Bearer {token}"},
                now=103,
            )
        self.assertEqual(allowed.status, 200)
        self.assertEqual(denied.status, 403)
        self.assertEqual(denied.payload["code"], "permission_denied")

    def test_package_list_signs_manifests_when_signing_identity_is_configured(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            api.signing_key = Ed25519PrivateKey.generate()
            api.signing_key_id = "key-1"
            api.packages.publish(
                PackageVersion("default.example", "1.0.0", "team.default.example", "a" * 64, 1, "published", 90,
                               PACKAGE_METADATA), now=90)
            exchanged = api.dispatch("exchange", "system.authentication.github", body=b'{"access_token":"token"}', now=99)
            issued = \
            api.key_issuer.create_batch(quantity=1, permissions=frozenset({"team.default.example.download"}), expires_at=None,
                                        actor="admin", now=90)[0]
            redeem = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                                           "Idempotency-Key": "sign-redeem"}, now=99)
            result = api.dispatch("read", "team.default.packages.catalog",
                                  headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=100)
        self.assertEqual(result.status, 200)
        self.assertEqual(len(result.payload["packages"]), 1)
        self.assertIn("signature", result.payload["packages"][0])

    def test_package_list_applies_assignment_deny(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            api.key_issuer.database.claim_first_owner("123", now=1)
            api.authentication.github = DynamicGitHub("456")
            api.packages.publish(
                PackageVersion("default.example", "1.0.0", "team.default.example", "a" * 64, 1,
                               "published", 90, PACKAGE_METADATA), now=90)
            exchanged = api.dispatch("exchange", "system.authentication.github",
                                     body=b'{"access_token":"token"}', now=99)
            issued = api.key_issuer.create_batch(
                quantity=1, permissions=frozenset({"team.default.example.download"}), expires_at=None,
                actor="admin", now=90)[0]
            redeem = api.dispatch("distribute", "system.keys.redeem", body=json.dumps({"key": issued.plaintext}).encode(),
                headers={"Authorization": f"Bearer {exchanged.payload['token']}",
                         "Idempotency-Key": "package-list-deny"}, now=99)
            self.assertEqual(redeem.status, 200)
            grant = next(item for item in api.authorization.grants("456", team_id="default")
                         if item.permissions == frozenset({"team.default.example.download"}))
            with api.key_issuer.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO grant_assignments
                       (assignment_id,grant_id,node,effect,priority,grant_effect,source_type,source_id)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    ("package-list-deny", grant.grant_id, "team.default.example.*", "deny", 10,
                     "allow", "test", "package-list-deny"),
                )
            result = api.dispatch(
                "read", "team.default.packages.catalog",
                headers={"Authorization": f"Bearer {exchanged.payload['token']}"}, now=100)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["packages"], [])

    def test_key_status_is_signed_and_time_bounded(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            api.signing_key = Ed25519PrivateKey.generate()
            api.signing_key_id = "key-1"
            result = api.dispatch("read", "system.key_status", now=100)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["status"]["active_key_id"], "key-1")
        self.assertEqual(result.payload["signature"]["key_id"], "key-1")

    def test_direct_upload_create_and_confirm_publishes_package(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            content = archive_bytes({"Mods/example.dll": b"payload"})
            exchanged = api.dispatch("exchange", "system.authentication.github",
                                     body=json.dumps({"access_token": "token"}).encode(), now=100)
            headers = {"Authorization": f"Bearer {exchanged.payload['token']}"}
            created = api.dispatch("create", "team.default.package_uploads", body=json.dumps(
                {"package_id": "default.example", "version": "1.0.0", "size": len(content),
                 "content_type": "application/zip",
                 "metadata": {"install": {"files": [{"match": "Mods/*.dll", "type": "melonloader:mod"}],
                                          "scan_dlls": True, "exclude": []}}}).encode(),
                headers={**headers, "Idempotency-Key": "upload-create-example"}, now=100)
            malicious = api.dispatch("create", "team.default.package_uploads", body=json.dumps(
                {"package_id": "default.other", "version": "1.0.0", "permission_node": "system.*",
                 "size": len(content), "content_type": "application/zip"}).encode(),
                headers={**headers, "Idempotency-Key": "upload-create-other"}, now=100)
            upload_id = created.payload["upload_id"]
            object_key = created.payload["upload"]["object_key"]
            self.assertEqual(created.payload["upload"]["upload_url"],
                             f"/v1/package-uploads/{object_key}")
            (Path(directory) / "objects" / "uploads").mkdir(parents=True, exist_ok=True)
            (Path(directory) / "objects" / "uploads" / object_key).write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            confirmed = api.dispatch("confirm", "team.default.package_uploads", data={"upload_id": upload_id},
                                     body=json.dumps({"sha256": digest}).encode(),
                                     headers={**headers, "Idempotency-Key": "upload-confirm-example"}, now=101)
        self.assertEqual(created.status, 201)
        self.assertEqual(created.payload["permission_node"], "team.default.example")
        self.assertEqual(malicious.payload["permission_node"], "team.default.other")
        self.assertEqual(confirmed.status, 201)
        self.assertEqual(confirmed.payload["id"], "default.example")


if __name__ == "__main__":
    unittest.main()
