from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.modules.packages.files import PAYLOAD_DLL
from sprocket_access_server.modules.packages.store import PackageVersion, SQLitePackageStore
from tests.support.schema_check import SchemaError, load_schema, validate

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
# 条目就是公开 v3 条目：这份文档是客户端仓 schemas/sprocket-mod.schema.json 的逐字镜像。
ENTRY_SCHEMA = SCHEMAS / "sprocket-mod.schema.json"
INDEX_SCHEMA = SCHEMAS / "sprocket-private-index.schema.json"

INSTALL = {"files": [{"match": "*.dll", "type": "melonloader:mod"}], "scan_dlls": True, "exclude": []}
# 版本行记录包元数据的快照，因此同样带规则。
SNAPSHOT = {"install": INSTALL}
# 服务端自己的公开 origin：下载端点的绝对 URL 由它拼出。
SERVER_ORIGIN = "https://server.test"


def build_record():
    directory = TemporaryDirectory()
    store = SQLitePackageStore(SQLiteDatabase(Path(directory.name) / "access.db"))
    store.create_package(
        "default.example", team_id="default", name="Example",
        metadata={"authors": ["alice"], "license": "MIT", "category": "gameplay",
                  "install": INSTALL, "tags": ["demo"]},
    )
    store.publish(
        PackageVersion("default.example", "1.0.0", "team.default.example", "a" * 64, 10, "published", 5,
                       SNAPSHOT),
        now=5, team_id="default",
    )
    store.publish(
        PackageVersion("default.example", "1.1.0", "team.default.example", "c" * 64, 11, "published", 6,
                       SNAPSHOT),
        now=6, team_id="default",
    )
    return directory, store.record("default.example", team_id="default")


class PublicEntryShapeTests(unittest.TestCase):
    def test_real_entry_matches_the_public_v3_schema(self) -> None:
        directory, record = build_record()
        self.addCleanup(directory.cleanup)
        entry = record.signed_entry(Ed25519PrivateKey.generate(), "key-1", download_base_url=SERVER_ORIGIN)
        validate(entry, load_schema(ENTRY_SCHEMA), base=SCHEMAS)
        self.assertEqual(entry["schema_version"], 3)
        # 一包一条目、多版本：两个 release 各自只是一个版本。
        self.assertEqual([item["version"] for item in entry["releases"]], ["1.0.0", "1.1.0"])
        self.assertEqual([item["id"] for item in entry["releases"]], [1, 2])
        self.assertNotIn("files", entry["releases"][0])
        # 仓库未知时不发这个键（v3 只收 <owner>/<name>）。
        self.assertNotIn("repository", entry)
        self.assertEqual(entry["releases"][0]["assets"][0]["download_url"],
                         f"{SERVER_ORIGIN}/v1/packages/default.example/download")

    def test_real_entry_with_every_optional_field_matches_the_public_v3_schema(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLitePackageStore(SQLiteDatabase(Path(directory) / "access.db"))
            store.create_package(
                "default.example", team_id="default", name="Example",
                metadata={"authors": ["alice", "bob"], "repository": "alice/example", "license": "MIT",
                          "display_name": {"en": "Example", "zh": "示例"},
                          "description": {"en": "An example", "zh": "一个示例"},
                          "dependencies": [{"id": "lavagang.melonloader", "version": ">=0.7.3", "when": "always"}],
                          "recommendations": ["lavagang.melonloader"], "install": INSTALL,
                          "category": "utility", "tags": ["demo", "ui"]},
            )
            store.publish(
                PackageVersion("default.example", "2.0.0", "team.default.example", "a" * 64, 10,
                               "published", 5, SNAPSHOT),
                now=5, team_id="default",
            )
            entry = store.record("default.example", team_id="default").signed_entry(
                Ed25519PrivateKey.generate(), "key-1", download_base_url=SERVER_ORIGIN,
            )
        validate(entry, load_schema(ENTRY_SCHEMA), base=SCHEMAS)
        self.assertEqual(entry["repository"], "alice/example")
        self.assertEqual(entry["authors"], ["alice", "bob"])
        self.assertEqual(entry["recommendations"], ["lavagang.melonloader"])
        # 包级依赖带 `when`；release 级只留公开 release 形状 {id, version}。
        self.assertEqual(entry["dependencies"], [
            {"id": "lavagang.melonloader", "version": ">=0.7.3", "when": "always"},
        ])
        self.assertEqual(entry["releases"][0]["dependencies"], [
            {"id": "lavagang.melonloader", "version": ">=0.7.3"},
        ])

    def test_entry_without_authors_omits_the_key(self) -> None:
        directory, record = build_record()
        self.addCleanup(directory.cleanup)
        entry = record.entry(download_base_url=SERVER_ORIGIN) | {"authors": []}
        # 公开 v3 的 authors 出现就至少一项。
        with self.assertRaisesRegex(SchemaError, "at least 1 items"):
            validate(entry, load_schema(ENTRY_SCHEMA), base=SCHEMAS)

    def test_single_dll_entry_matches_the_public_v3_schema(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLitePackageStore(SQLiteDatabase(Path(directory) / "access.db"))
            store.create_package("default.example", team_id="default", name="Example",
                                 metadata={"install": INSTALL})
            store.publish(
                PackageVersion("default.example", "1.0.0", "team.default.example", "a" * 64, 10,
                               "published", 5, SNAPSHOT, payload_kind=PAYLOAD_DLL),
                now=5, team_id="default",
            )
            entry = store.record("default.example", team_id="default").signed_entry(
                Ed25519PrivateKey.generate(), "key-1", download_base_url=SERVER_ORIGIN,
            )
        release = entry["releases"][0]
        self.assertEqual(release["assets"][0]["name"], "default.example.dll")
        self.assertNotIn("files", release)
        validate(entry, load_schema(ENTRY_SCHEMA), base=SCHEMAS)

    def test_real_index_matches_the_index_schema(self) -> None:
        directory, record = build_record()
        self.addCleanup(directory.cleanup)
        index = {
            "schema_version": 1,
            "generated_at": "2026-10-02T11:30:00Z",
            "server": {"server_id": "server-1", "name": "Local server"},
            "teams": [{
                "team_id": "default",
                "name": "Default Team",
                "packages": [record.signed_entry(Ed25519PrivateKey.generate(), "key-1",
                                                 download_base_url=SERVER_ORIGIN)],
            }],
        }
        validate(index, load_schema(INDEX_SCHEMA), base=SCHEMAS)

    def test_schema_rejects_entries_the_client_refuses(self) -> None:
        directory, record = build_record()
        self.addCleanup(directory.cleanup)
        entry = record.entry(download_base_url=SERVER_ORIGIN)
        schema = load_schema(ENTRY_SCHEMA)

        missing_install = {key: value for key, value in entry.items() if key != "install"}
        with self.assertRaisesRegex(SchemaError, "required property 'install' is missing"):
            validate(missing_install, schema, base=SCHEMAS)

        old_version = dict(entry, schema_version=2)
        with self.assertRaisesRegex(SchemaError, "expected 3"):
            validate(old_version, schema, base=SCHEMAS)

        bad_id = dict(entry, id="Default.Example")
        with self.assertRaisesRegex(SchemaError, "does not match"):
            validate(bad_id, schema, base=SCHEMAS)

        bad_category = dict(entry, category="Gameplay")
        with self.assertRaisesRegex(SchemaError, "is not one of"):
            validate(bad_category, schema, base=SCHEMAS)

        string_dependency = dict(entry, dependencies=["default.core"])
        with self.assertRaisesRegex(SchemaError, "expected object"):
            validate(string_dependency, schema, base=SCHEMAS)

        integer_time = dict(entry)
        integer_time["releases"] = [dict(entry["releases"][0], published_at=1790905374)]
        with self.assertRaisesRegex(SchemaError, "expected string"):
            validate(integer_time, schema, base=SCHEMAS)

    def test_repository_accepts_an_owner_and_name(self) -> None:
        directory, record = build_record()
        self.addCleanup(directory.cleanup)
        schema = load_schema(ENTRY_SCHEMA)
        for repository in ("alice/example", "BepInEx/BepInEx", "alice/example.mod", "a.b/c-d"):
            with self.subTest(repository=repository):
                validate(record.entry(download_base_url=SERVER_ORIGIN) | {"repository": repository},
                         schema, base=SCHEMAS)

    def test_download_url_is_absolute(self) -> None:
        directory, record = build_record()
        self.addCleanup(directory.cleanup)
        for base in ("https://server.test", "https://server.test:8443", "https://[::1]:8443",
                     "http://127.0.0.1:8787", "http://localhost", "http://[::1]:8787"):
            with self.subTest(base=base):
                entry = record.entry(download_base_url=base)
                validate(entry, load_schema(ENTRY_SCHEMA), base=SCHEMAS)
                self.assertEqual(entry["releases"][0]["assets"][0]["download_url"],
                                 f"{base}/v1/packages/default.example/download")


class IndexSchemaTests(unittest.TestCase):
    def test_index_schema_points_at_the_public_entry_schema(self) -> None:
        index_schema = load_schema(INDEX_SCHEMA)
        self.assertEqual(set(index_schema["properties"]),
                         {"schema_version", "generated_at", "server", "teams"})
        teams = index_schema["properties"]["teams"]["items"]
        self.assertEqual(set(teams["properties"]), {"team_id", "name", "packages"})
        self.assertEqual(teams["required"], ["team_id", "name", "packages"])
        self.assertEqual(teams["properties"]["packages"]["items"], {"$ref": "sprocket-mod.schema.json"})

    def test_vendored_entry_schema_is_the_public_v3_document(self) -> None:
        entry_schema = load_schema(ENTRY_SCHEMA)
        self.assertEqual(entry_schema["$id"],
                         "https://sprocketmods.furryaxw.top/schemas/sprocket-mod.schema.json")
        self.assertEqual(entry_schema["properties"]["schema_version"], {"const": 3})
        self.assertNotIn("additionalProperties", entry_schema)

    def test_index_schema_rejects_a_top_level_package_list(self) -> None:
        directory, record = build_record()
        self.addCleanup(directory.cleanup)
        index = {
            "schema_version": 1,
            "generated_at": "2026-10-02T11:30:00Z",
            "server": {"server_id": "server-1", "name": "Local server"},
            "teams": [{"team_id": "default", "name": "Default Team",
                       "packages": [record.entry(download_base_url=SERVER_ORIGIN)]}],
            "packages": [record.entry(download_base_url=SERVER_ORIGIN)],
        }
        with self.assertRaisesRegex(SchemaError, "additional property"):
            validate(index, load_schema(INDEX_SCHEMA), base=SCHEMAS)


if __name__ == "__main__":
    unittest.main()
