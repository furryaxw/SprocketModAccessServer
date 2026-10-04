from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sprocket_access_server.core.ports import GitHubIdentity
from sprocket_access_server.domain.protocol import ServerInfo
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.network import ResourceRegistry
from sprocket_access_server.infrastructure.security.sessions import SQLiteSessionStore
from sprocket_access_server.modules.packages.store import PackageVersion, SQLitePackageStore
from sprocket_access_server.modules.system.authentication import AuthenticationService
from sprocket_access_server.presentation.asgi import create_app
from tests.support.schema_check import load_schema, validate
from tests.support.v1_http_client import json_call

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
# 条目就是公开 v3 条目：这份文档是客户端仓 schemas/sprocket-mod.schema.json 的逐字镜像。
ENTRY_SCHEMA = SCHEMAS / "sprocket-mod.schema.json"
INDEX_SCHEMA = SCHEMAS / "sprocket-private-index.schema.json"

INSTALL = {"files": [{"match": "*.dll", "type": "melonloader:mod"}], "scan_dlls": True, "exclude": []}
SNAPSHOT = {"install": INSTALL}
DEPENDENCIES = [{"id": "lavagang.melonloader", "version": ">=0.7.3", "when": "always"}]


class FakeGitHub:
    def verify_access_token(self, _token: str) -> GitHubIdentity:
        return GitHubIdentity("123", "admin")


class FakeAuthorization:
    """只实现 `/v1/packages` 用到的两个入口。"""

    def memberships(self, _user_id: str):
        return [
            {"team_id": "default", "name": "Default Team"},
            {"team_id": "quiet", "name": "Quiet Team"},
            {"team_id": "system", "name": "System"},
            {"team_id": "template", "name": "Template Team"},
        ]

    def allows(self, _user_id: str, _permission: str, *, now=None, team_id=None) -> bool:
        return True


class PrivateIndexHttpShapeTests(unittest.TestCase):
    def _app(self, directory: str, *, public_base_url: str = ""):
        database = SQLiteDatabase(Path(directory) / "access.db")
        sessions = SQLiteSessionStore(database, b"pepper")
        session = sessions.create("123", ttl=10 ** 9)
        store = SQLitePackageStore(database)
        store.create_package("default.example", team_id="default", name="Example",
                             metadata={"install": INSTALL, "dependencies": DEPENDENCIES})
        store.publish(
            PackageVersion("default.example", "1.0.0", "team.default.example", "a" * 64, 10,
                           "published", 5, SNAPSHOT),
            now=5, team_id="default",
        )
        services = {
            "authentication": AuthenticationService(FakeGitHub(), sessions),
            "authorization_service": FakeAuthorization(),
            "packages": store,
            "server_info": ServerInfo("server-1", "Test", None, {}),
            # 未配置签名身份：走未签名的条目分支。
            "signing_key": None,
            "signing_key_id": None,
        }
        if public_base_url:
            services["publisher"] = SimpleNamespace(download_base_url=public_base_url)
        context = SimpleNamespace(
            resources=ResourceRegistry(),
            service=lambda name: services[name],
        )
        app = create_app(ServerInfo("server-1", "Test", None, {}),
                         context.service("authentication"), module_context=context)
        return app, session

    def test_index_groups_packages_under_their_team(self) -> None:
        with TemporaryDirectory() as directory:
            app, session = self._app(directory)
            # 线上是 https；本机开发用 loopback http —— 两者都必须过客户端那条 download_url 规则。
            for scheme, host, expected_origin in (
                    ("https", "server.test", "https://server.test"),
                    ("http", "127.0.0.1:8787", "http://127.0.0.1:8787")):
                with self.subTest(origin=expected_origin):
                    status, payload = json_call(
                        app, "GET", "/v1/packages", scheme=scheme,
                        headers={"authorization": f"Bearer {session['token']}", "host": host},
                    )
                    self.assertEqual(status, 200)
                    entry = payload["teams"][0]["packages"][0]
                    asset = entry["releases"][0]["assets"][0]
                    self.assertEqual(asset["download_url"],
                                     f"{expected_origin}/v1/packages/default.example/download")
                    # 真实发出的整份索引过权威 schema：每个 download_url 都被正则校验。
                    validate(payload, load_schema(INDEX_SCHEMA), base=SCHEMAS)
                    validate(entry, load_schema(ENTRY_SCHEMA), base=SCHEMAS)

        # 索引只有 Team 列表：保留 workspace 不出现，其余 Team 一律带 packages 键。
        self.assertEqual(set(payload), {"schema_version", "generated_at", "server", "teams"})
        self.assertEqual(
            [(team["team_id"], len(team["packages"])) for team in payload["teams"]],
            [("default", 1), ("quiet", 0)],
        )

    def test_index_uses_the_declared_public_origin_whenever_it_is_configured(self) -> None:
        with TemporaryDirectory() as directory:
            app, session = self._app(directory, public_base_url="https://raw.example.test:28482")
            # 配了就一律用它：请求 origin 可用（这里是另一个 https origin，本可原样使用）时不拿它，
            # 不可用（明文 http 加非 loopback 主机名，反向代理终止 TLS 时服务端看到的就是这种请求）
            # 时更不拿它。
            for scheme, host in (("https", "internal.example.test:9443"), ("http", "raw.example.test")):
                with self.subTest(origin=f"{scheme}://{host}"):
                    status, payload = json_call(
                        app, "GET", "/v1/packages", scheme=scheme,
                        headers={"authorization": f"Bearer {session['token']}", "host": host},
                    )
                    self.assertEqual(status, 200)
                    asset = payload["teams"][0]["packages"][0]["releases"][0]["assets"][0]
                    self.assertEqual(asset["download_url"],
                                     "https://raw.example.test:28482/v1/packages/default.example/download")
                    validate(payload, load_schema(INDEX_SCHEMA), base=SCHEMAS)

    def test_index_keeps_the_request_origin_when_no_public_origin_is_declared(self) -> None:
        with TemporaryDirectory() as directory:
            app, session = self._app(directory)
            status, payload = json_call(
                app, "GET", "/v1/packages", scheme="http",
                headers={"authorization": f"Bearer {session['token']}", "host": "raw.example.test"},
            )
            self.assertEqual(status, 200)
            asset = payload["teams"][0]["packages"][0]["releases"][0]["assets"][0]
            # 没有可替代的声明时仍给请求 origin，并留一条 WARNING 让人知道客户端会拒这个地址。
            self.assertEqual(asset["download_url"],
                             "http://raw.example.test/v1/packages/default.example/download")

    def test_entry_release_and_asset_keys_are_the_public_v3_shape(self) -> None:
        with TemporaryDirectory() as directory:
            app, session = self._app(directory)
            _status, payload = json_call(
                app, "GET", "/v1/packages",
                headers={"authorization": f"Bearer {session['token']}", "host": "server.test"},
            )

        entry = payload["teams"][0]["packages"][0]
        # 条目就是公开 v3 条目（私有只加 signature，这里未签名）。
        self.assertEqual(set(entry), {
            "schema_version", "id", "name", "license", "display_name",
            "description", "dependencies", "recommendations", "install", "category", "tags", "releases",
        })
        self.assertEqual(entry["schema_version"], 3)
        # 没有作者时省略 authors（v3 出现就至少一项），仓库未知时同样不出现。
        self.assertNotIn("authors", entry)
        self.assertNotIn("repository", entry)
        self.assertEqual(entry["dependencies"], DEPENDENCIES)
        release = entry["releases"][0]
        self.assertEqual(set(release),
                         {"id", "tag", "version", "prerelease", "published_at", "assets", "dependencies",
                          "compatibility"})
        asset = release["assets"][0]
        self.assertEqual(set(asset), {"id", "name", "size", "download_url", "digest", "updated_at"})
        self.assertEqual(release["compatibility"], {"source": "declared"})
        # 发布级依赖只有 {id, version}：包级的 `when` 不外泄。
        self.assertEqual(release["dependencies"], [
            {"id": "lavagang.melonloader", "version": ">=0.7.3"},
        ])


if __name__ == "__main__":
    unittest.main()
