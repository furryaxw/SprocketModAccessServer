from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.core.ports import GitHubIdentity
from sprocket_access_server.domain.protocol import ServerInfo
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.events import ResourceChanged
from sprocket_access_server.infrastructure.permissions import PermissionCatalog
from sprocket_access_server.infrastructure.security.sessions import SQLiteSessionStore
from sprocket_access_server.infrastructure.storage.objects import LocalFileObjectStorage
from sprocket_access_server.infrastructure.storage.uploads import SQLiteUploadStore
from sprocket_access_server.infrastructure.utilities.confirmations import SQLiteConfirmationStore
from sprocket_access_server.infrastructure.utilities.idempotency import SQLiteIdempotencyStore
from sprocket_access_server.modules.audit.store import SQLiteAuditStore
from sprocket_access_server.modules.keys.store import KeyIssuer
from sprocket_access_server.modules.packages.download_tokens import DownloadTokenCodec
from sprocket_access_server.modules.packages.files import PAYLOAD_DLL, PAYLOAD_ZIP
from sprocket_access_server.modules.packages.publication import PackagePublisher
from sprocket_access_server.modules.packages.store import PackageVersion, SQLitePackageStore
from sprocket_access_server.modules.permission_assignments.provisioning import upsert_assignment_grant
from sprocket_access_server.modules.permission_assignments.store import SQLiteAuthorizationStore
from sprocket_access_server.modules.permission_templates.store import SQLiteGrantTemplateStore
from sprocket_access_server.modules.system.authentication import AuthenticationService
from sprocket_access_server.modules.system.platform import PlatformStore
from sprocket_access_server.modules.system.provisioning import SystemProvisioning
from tests.support.resource_harness import ResourceDispatchHarness

INSTALL = {"files": [{"match": "*.dll", "type": "melonloader:mod"}], "scan_dlls": True, "exclude": []}
SNAPSHOT = {"install": INSTALL, "channel": "stable"}
TEAM_ID = "alpha"
PACKAGE_ID = "alpha.example"
MANAGE_NODES = (
    "team.alpha.packages.read",
    "team.alpha.packages.manage",
    "team.alpha.packages.confirm",
    "team.alpha.packages.download",
)


class FakeGitHub:
    def verify_access_token(self, _token: str) -> GitHubIdentity:
        return GitHubIdentity("123", "admin")


class PackageVersionMutationTests(unittest.TestCase):
    """管理端对已发布版本的修改与删除。

    团队行在装配之前就存在，因此这批用例打到的是模块自己注册的 `team.alpha.packages`
    处理器（而不是测试适配器补的那套）。
    """

    def api(self, directory: str, *, nodes: tuple[str, ...] = MANAGE_NODES,
            reader_nodes: tuple[str, ...] = ("team.alpha.packages.read",)) -> ResourceDispatchHarness:
        database = SQLiteDatabase(Path(directory) / "access.db")
        database.initialize()
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO users(github_user_id,login_snapshot,created_at,updated_at) "
                "VALUES('123','admin',1,1)")
            connection.execute(
                "INSERT INTO users(github_user_id,login_snapshot,created_at,updated_at) "
                "VALUES('456','reader',1,1)")
            connection.execute(
                "INSERT INTO teams(team_id,name,description,status,owner_user_id,created_by,created_at,updated_at) "
                "VALUES('alpha','Alpha','','active','123','123',1,1)")
            upsert_assignment_grant(
                connection, grant_id="direct:123", user_id="123", team_id=TEAM_ID,
                nodes=nodes, source_type="direct", source_id="system", created_by="system", now=2,
            )
            upsert_assignment_grant(
                connection, grant_id="direct:456", user_id="456", team_id=TEAM_ID,
                nodes=reader_nodes, source_type="direct", source_id="system", created_by="system", now=2,
            )
        packages = SQLitePackageStore(database)
        storage = LocalFileObjectStorage(Path(directory) / "objects")
        return ResourceDispatchHarness(
            ServerInfo("server-1", "Test", None, {}),
            AuthenticationService(
                FakeGitHub(), SQLiteSessionStore(database, b"pepper"), session_ttl=100,
                ownership=database, provisioning=SystemProvisioning(database, "simple"),
            ),
            key_issuer=KeyIssuer(database, SQLiteAuthorizationStore(database), b"key-pepper"),
            idempotency=SQLiteIdempotencyStore(database),
            authorization=SQLiteAuthorizationStore(database),
            packages=packages,
            audit=SQLiteAuditStore(database),
            download_tokens=DownloadTokenCodec(b"download-secret"),
            upload_store=SQLiteUploadStore(database),
            direct_storage=storage,
            publisher=PackagePublisher(packages, storage),
            permission_templates=SQLiteGrantTemplateStore(database),
            confirmations=SQLiteConfirmationStore(database),
            permission_catalog=PermissionCatalog(database),
            platform=PlatformStore(database, "simple"),
        )

    @staticmethod
    def publish(api: ResourceDispatchHarness, version: str, *, payload_kind: str = PAYLOAD_ZIP,
                digest: str = "") -> None:
        api.packages.publish(
            PackageVersion(
                PACKAGE_ID, version, "team.alpha.example",
                digest or hashlib.sha256(version.encode("utf-8")).hexdigest(), 10,
                "published", 100, dict(SNAPSHOT), TEAM_ID, payload_kind,
            ),
            now=100, team_id=TEAM_ID,
        )

    @staticmethod
    def stage_archive(directory: str, digest: str) -> Path:
        """把归档字节放进本地存储（与包行同样的内容寻址布局），回收才有的可删。"""
        target = Path(directory) / "objects" / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"archive")
        return target

    def register_package_node(self, api: ResourceDispatchHarness) -> None:
        """走真实路径注册包节点：`store.publish` 的事件在测试装配里没有总线，这里手动补一次。"""
        api.module_context.events.publish(ResourceChanged(
            kind="create", node=f"team.{TEAM_ID}.packages", action="create",
            data={"package_id": PACKAGE_ID}, team_id=TEAM_ID,
        ))
        self.assertIsNotNone(api.resources.resolve("manage", f"team.{TEAM_ID}.example"))

    def headers(self, api: ResourceDispatchHarness, user: str, *, request_key: str = "") -> dict[str, str]:
        token = api.authentication.sessions.create(user, ttl=1000, now=100)["token"]
        headers = {"Authorization": f"Bearer {token}", "X-Team-Id": TEAM_ID}
        if request_key:
            headers["Idempotency-Key"] = request_key
        return headers

    def token_for(self, api: ResourceDispatchHarness, user: str, data: dict[str, object]) -> str:
        issued = api.dispatch("confirm", "team.alpha.packages", data=data, headers=self.headers(api, user), now=101)
        self.assertEqual(issued.status, 200, issued.payload)
        return str(issued.payload["confirmation_token"])

    def versions(self, api: ResourceDispatchHarness, user: str = "123") -> list[dict[str, object]]:
        read = api.dispatch("read", "team.alpha.packages", data={"limit": 50, "offset": 0},
                            headers=self.headers(api, user), now=102)
        self.assertEqual(read.status, 200, read.payload)
        packages = read.payload["packages"]
        self.assertEqual(len(packages), 1)
        return packages[0]["versions"]

    def test_version_status_change_requires_a_confirmation_token(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            self.publish(api, "1.0.0")
            preview = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0", "status": "disabled", "preview": True},
                headers=self.headers(api, "123", request_key="preview"), now=101,
            )
            unconfirmed = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0", "metadata": {}, "status": "disabled"},
                headers=self.headers(api, "123", request_key="apply"), now=101,
            )
            applied = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0", "metadata": {}, "status": "disabled",
                      "confirmation_token": self.token_for(api, "123", {"package_id": PACKAGE_ID, "version": "1.0.0"})},
                headers=self.headers(api, "123", request_key="apply-confirmed"), now=101,
            )
            stored_status = self.versions(api)[0]["status"]

        self.assertEqual(preview.status, 200)
        self.assertTrue(preview.payload["requires_confirmation"])
        self.assertFalse(preview.payload["delete"])
        self.assertEqual(unconfirmed.status, 400)
        self.assertEqual(unconfirmed.payload["code"], "confirmation_required")
        self.assertEqual(applied.status, 200)
        self.assertEqual(stored_status, "disabled")

    def test_version_metadata_is_replaced_and_keeps_the_payload_kind(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            self.publish(api, "1.0.0", payload_kind=PAYLOAD_DLL)
            replaced = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0",
                      "metadata": {"install": INSTALL, "channel": "beta"}},
                headers=self.headers(api, "123", request_key="metadata"), now=101,
            )
            stored = api.packages.get(PACKAGE_ID, "1.0.0", team_id=TEAM_ID)
            listed = self.versions(api)[0]

        self.assertEqual(replaced.status, 200, replaced.payload)
        # 快照被整体替换：旧的 `channel` 不在新对象里。
        self.assertEqual(stored.metadata, {"install": INSTALL, "channel": "beta"})
        # 载荷形态记在版本行上而不是元数据里：替换不能把它丢掉，否则资产扩展名会退回 `.zip`。
        self.assertEqual(stored.payload_kind, PAYLOAD_DLL)
        self.assertEqual(replaced.payload["releases"][0]["assets"][0]["name"], f"{PACKAGE_ID}.dll")
        # 管理端的版本行不含版本元数据：条目读的是包实体元数据，这份快照不外显，也就不给编辑入口。
        self.assertNotIn("metadata", listed)

    def test_version_metadata_must_stay_publishable(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            self.publish(api, "1.0.0")
            rejected = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0", "metadata": {"channel": "beta"}},
                headers=self.headers(api, "123", request_key="metadata-invalid"), now=101,
            )

        self.assertEqual(rejected.status, 400)
        self.assertEqual(rejected.payload["code"], "invalid_request")
        self.assertIn("install", rejected.payload["message"])

    def test_version_delete_frees_the_version_number(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            self.publish(api, "1.0.0")
            preview = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0", "delete": True, "preview": True},
                headers=self.headers(api, "123", request_key="delete-preview"), now=101,
            )
            deleted = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0", "delete": True,
                      "confirmation_token": self.token_for(
                          api, "123", {"package_id": PACKAGE_ID, "version": "1.0.0"})},
                headers=self.headers(api, "123", request_key="delete-apply"), now=101,
            )
            # 包实体留下：没有版本的包可以再上传同一个版本号。
            record = api.packages.record(PACKAGE_ID, team_id=TEAM_ID)
            self.publish(api, "1.0.0")
            republished = api.packages.get(PACKAGE_ID, "1.0.0", team_id=TEAM_ID)
            packages, total = api.packages.search_records(team_id=TEAM_ID)
            entries = api.packages.records(team_ids=(TEAM_ID,))

        self.assertEqual(preview.status, 200)
        self.assertTrue(preview.payload["delete"])
        self.assertTrue(preview.payload["requires_confirmation"])
        self.assertEqual(deleted.status, 200)
        self.assertEqual(deleted.payload["versions"], [])
        self.assertIsNotNone(record)
        self.assertEqual(record.versions, ())
        self.assertIsNotNone(republished)
        self.assertEqual((len(packages), total), (1, 1))
        self.assertEqual(len(entries), 1)

    def test_version_delete_needs_a_token_and_the_matching_target(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            self.publish(api, "1.0.0")
            unconfirmed = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0", "delete": True},
                headers=self.headers(api, "123", request_key="no-token"), now=101,
            )
            # 整包删除的令牌目标不带版本，不能拿来删版本。
            package_token = self.token_for(api, "123", {"package_id": PACKAGE_ID})
            mismatched = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0", "delete": True,
                      "confirmation_token": package_token},
                headers=self.headers(api, "123", request_key="wrong-token"), now=101,
            )
            still_there = api.packages.get(PACKAGE_ID, "1.0.0", team_id=TEAM_ID)

        self.assertEqual(unconfirmed.status, 400)
        self.assertEqual(unconfirmed.payload["code"], "confirmation_required")
        self.assertEqual(mismatched.status, 400)
        self.assertEqual(mismatched.payload["code"], "confirmation_required")
        self.assertIsNotNone(still_there)

    def test_package_delete_removes_versions_and_its_catalog_nodes(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            self.publish(api, "1.0.0")
            self.publish(api, "1.1.0")
            preview = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "delete": True, "preview": True},
                headers=self.headers(api, "123", request_key="package-preview"), now=101,
            )
            deleted = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "delete": True,
                      "confirmation_token": self.token_for(api, "123", {"package_id": PACKAGE_ID})},
                headers=self.headers(api, "123", request_key="package-delete"), now=101,
            )
            read = api.dispatch("read", "team.alpha.packages", data={"limit": 50, "offset": 0},
                                headers=self.headers(api, "123"), now=102)
            _records, total = api.packages.search_records(team_id=TEAM_ID)
            with api.key_issuer.database.transaction() as connection:
                leftovers = connection.execute(
                    "SELECT node FROM permission_nodes WHERE node LIKE ?", (f"team.{TEAM_ID}.example%",)
                ).fetchall()

        self.assertEqual(preview.status, 200)
        self.assertEqual(preview.payload["versions"],
                         [{"version": "1.0.0", "status": "published"}, {"version": "1.1.0", "status": "published"}])
        self.assertEqual(deleted.status, 200)
        self.assertEqual(deleted.payload["versions"], ["1.0.0", "1.1.0"])
        self.assertEqual(read.payload["packages"], [])
        self.assertEqual(total, 0)
        self.assertEqual(leftovers, [])

    def test_version_delete_reclaims_only_unreferenced_archives(self) -> None:
        digest = "d" * 64
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            self.stage_archive(directory, digest)
            self.publish(api, "1.0.0", digest=digest)
            self.publish(api, "1.1.0", digest=digest)
            first = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0", "delete": True,
                      "confirmation_token": self.token_for(
                          api, "123", {"package_id": PACKAGE_ID, "version": "1.0.0"})},
                headers=self.headers(api, "123", request_key="reclaim-1"), now=101,
            )
            # 同一份字节还被 1.1.0 引用：不能删。
            shared = (Path(directory) / "objects" / digest).is_file()
            second = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.1.0", "delete": True,
                      "confirmation_token": self.token_for(
                          api, "123", {"package_id": PACKAGE_ID, "version": "1.1.0"})},
                headers=self.headers(api, "123", request_key="reclaim-2"), now=102,
            )
            reclaimed = (Path(directory) / "objects" / digest).is_file()

        self.assertEqual(first.status, 200, first.payload)
        self.assertTrue(shared)
        self.assertEqual(second.status, 200, second.payload)
        self.assertFalse(reclaimed)

    def test_package_delete_clears_permission_rows_and_reclaims_archives(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            self.publish(api, "1.0.0")
            self.register_package_node(api)
            digest = api.packages.get(PACKAGE_ID, "1.0.0", team_id=TEAM_ID).archive_digest
            self.stage_archive(directory, digest)
            with api.key_issuer.database.transaction() as connection:
                # tester scope：老 Team 成员手里那种"只挂这个包节点"的授权。
                connection.execute(
                    """INSERT INTO grants
                           (grant_id,github_user_id,source_type,source_id,team_id,status,created_by,
                            created_at,updated_at)
                       VALUES('tester:alpha:456','456','tester_scope','tester','alpha','active','system',1,1)""",
                )
                connection.execute(
                    """INSERT INTO grant_assignments
                           (assignment_id,grant_id,node,effect,priority,grant_effect,source_type,source_id)
                       VALUES('tester:alpha:456:0','tester:alpha:456','team.alpha.example.read','allow',0,'allow',
                              'tester_scope','tester')""",
                )
                # 混在别的节点里的授权行只删该行，授权本身保留。
                connection.execute(
                    """INSERT INTO grants
                           (grant_id,github_user_id,source_type,source_id,team_id,status,created_by,
                            created_at,updated_at)
                       VALUES('mixed:alpha:456','456','direct','system','alpha','active','system',1,1)""",
                )
                for index, node in enumerate(("team.alpha.packages.read", "team.alpha.example.download")):
                    connection.execute(
                        """INSERT INTO grant_assignments
                               (assignment_id,grant_id,node,effect,priority,grant_effect,source_type,source_id)
                           VALUES(?,?,?,'allow',0,'allow','direct','system')""",
                        (f"mixed:alpha:456:{index}", "mixed:alpha:456", node),
                    )
                connection.execute(
                    """INSERT INTO permission_templates
                           (template_id,name,template_kind,team_id,created_by,created_at)
                       VALUES('alpha:custom','Custom','permission_template','alpha','system',1)""",
                )
                connection.execute(
                    """INSERT INTO template_assignments
                           (assignment_id,template_id,node,effect,priority,grant_effect)
                       VALUES('alpha:custom:0','alpha:custom','team.alpha.example.read','allow',0,'allow')""",
                )
            deleted = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "delete": True,
                      "confirmation_token": self.token_for(api, "123", {"package_id": PACKAGE_ID})},
                headers=self.headers(api, "123", request_key="purge"), now=101,
            )
            with api.key_issuer.database.transaction() as connection:
                grants = sorted(
                    str(row["grant_id"]) for row in connection.execute(
                        "SELECT grant_id FROM grants WHERE team_id='alpha'").fetchall()
                )
                mixed = sorted(
                    str(row["node"]) for row in connection.execute(
                        "SELECT node FROM grant_assignments WHERE grant_id='mixed:alpha:456'").fetchall()
                )
                templates = int(connection.execute(
                    "SELECT COUNT(*) AS total FROM template_assignments WHERE template_id='alpha:custom'"
                ).fetchone()["total"])
            reclaimed = (Path(directory) / "objects" / digest).is_file()
            node_registered = api.resources.resolve("manage", f"team.{TEAM_ID}.example") is not None

        self.assertEqual(deleted.status, 200, deleted.payload)
        # 只剩空壳的 tester 授权整行删掉；混着别的节点的授权保留，只少了那个包节点。
        self.assertEqual(grants, ["direct:123", "direct:456", "mixed:alpha:456"])
        self.assertEqual(mixed, ["team.alpha.packages.read"])
        self.assertEqual(templates, 0)
        self.assertFalse(reclaimed)
        self.assertFalse(node_registered)

    def test_reader_cannot_delete_a_version(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            self.publish(api, "1.0.0")
            denied = api.dispatch(
                "manage", "team.alpha.packages",
                data={"package_id": PACKAGE_ID, "version": "1.0.0", "delete": True,
                      "confirmation_token": "unused"},
                headers=self.headers(api, "456", request_key="reader-delete"), now=101,
            )
            still_there = api.packages.get(PACKAGE_ID, "1.0.0", team_id=TEAM_ID)

        self.assertEqual(denied.status, 403)
        self.assertEqual(denied.payload["code"], "team_permission_denied")
        self.assertIsNotNone(still_there)


if __name__ == "__main__":
    unittest.main()
