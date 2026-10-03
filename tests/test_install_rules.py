from __future__ import annotations

import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.storage.objects import LocalFileObjectStorage
from sprocket_access_server.modules.packages.files import validate_payload
from sprocket_access_server.modules.packages.metadata import validate_metadata
from sprocket_access_server.modules.packages.publication import PackagePublisher
from sprocket_access_server.modules.packages.store import SQLitePackageStore
from tests.support.schema_check import load_schema, validate

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
# 条目就是公开 v3 条目：这份文档是客户端仓 schemas/sprocket-mod.schema.json 的逐字镜像。
ENTRY_SCHEMA = SCHEMAS / "sprocket-mod.schema.json"

# 非 MelonLoader 的例子：BepInEx 插件的落点由客户端按它自己的加载器注册表解析。
FILE_RULES = {
    "files": [{"match": "*.dll", "type": "bepinex:plugin", "subpath": "MyMod", "layout": "file"}],
    "scan_dlls": True,
    "exclude": [],
}
PAYLOAD_RULES = {
    "payload": [{"match": "BepInEx/**", "target": "{Sprocket}/BepInEx/plugins", "layout": "tree"}],
    "exclude": [],
}


def archive_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


class InstallRuleValidationTests(unittest.TestCase):
    def test_accepts_the_typed_shapes(self) -> None:
        for install in (FILE_RULES, PAYLOAD_RULES,
                        {"files": [{"match": "*.dll", "type": "melonloader:*"}], "scan_dlls": False, "exclude": []},
                        {**FILE_RULES, "mode": "patch", "replace": ["bepinex:plugin"]}):
            with self.subTest(install=install):
                validate_metadata({"install": install})

    def test_rejects_mutually_exclusive_and_incomplete_rules(self) -> None:
        cases = {
            "files and payload": {"files": [{"match": "*.dll", "type": "a:b"}],
                                  "payload": [{"match": "*.dll", "target": "{Sprocket}/Mods"}],
                                  "scan_dlls": True, "exclude": []},
            "install without rules": {"scan_dlls": True, "exclude": []},
            "empty files": {"files": [], "scan_dlls": True, "exclude": []},
            "empty payload": {"payload": [], "exclude": []},
            "files without scan_dlls": {"files": [{"match": "*.dll", "type": "a:b"}], "exclude": []},
            "files without exclude": {"files": [{"match": "*.dll", "type": "a:b"}], "scan_dlls": True},
            "payload with scan_dlls": {"payload": [{"match": "*.dll", "target": "{Sprocket}/Mods"}],
                                       "exclude": [], "scan_dlls": True},
            "payload without exclude": {"payload": [{"match": "*.dll", "target": "{Sprocket}/Mods"}]},
            "replace without rules": {"replace": ["melonloader:mod"], "mode": "patch"},
            "replace without patch mode": {**FILE_RULES, "replace": ["melonloader:mod"]},
            "replace with standard mode": {**FILE_RULES, "replace": ["melonloader:mod"], "mode": "standard"},
            "empty replace": {**FILE_RULES, "replace": [], "mode": "patch"},
            "wildcard replace": {**FILE_RULES, "replace": ["melonloader:*"], "mode": "patch"},
        }
        for label, install in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                validate_metadata({"install": install})

    def test_rejects_malformed_rule_fields(self) -> None:
        cases = {
            "type without loader": {"files": [{"match": "*.dll", "type": "plugin"}], "scan_dlls": True, "exclude": []},
            "type with spaces": {"files": [{"match": "*.dll", "type": "BepInEx:Plugin"}], "scan_dlls": True,
                                 "exclude": []},
            "missing match": {"files": [{"type": "a:b"}], "scan_dlls": True, "exclude": []},
            "target without token": {"payload": [{"match": "*.dll", "target": "Mods"}], "exclude": []},
            "target with colon": {"payload": [{"match": "*.dll", "target": "{Sprocket}/a:b"}], "exclude": []},
            "bad subpath": {"files": [{"match": "*.dll", "type": "a:b", "subpath": "/abs"}], "scan_dlls": True,
                            "exclude": []},
            "bad layout": {"files": [{"match": "*.dll", "type": "a:b", "layout": "dir"}], "scan_dlls": True,
                           "exclude": []},
        }
        for label, install in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                validate_metadata({"install": install})


class PayloadValidationTests(unittest.TestCase):
    def test_rules_are_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "must declare install rules"):
            validate_payload(io.BytesIO(b"MZpayload"))
        with self.assertRaisesRegex(ValueError, "must declare install rules"):
            validate_payload(io.BytesIO(archive_bytes({"Mods/a.dll": b"x"})))

    def test_single_dll_only_needs_declared_rules(self) -> None:
        # 单个 DLL 的落点由规则描述：服务端不再要求显式 target。
        validate_payload(io.BytesIO(b"MZpayload"), install=FILE_RULES)

    def test_archive_entries_outside_the_mods_layout_pass_a_matching_rule(self) -> None:
        validate_payload(io.BytesIO(archive_bytes({"BepInEx/plugins/Example.dll": b"x"})), install=PAYLOAD_RULES)

    def test_archive_entries_without_a_rule_are_rejected(self) -> None:
        install = {"files": [{"match": "Mods/*.dll", "type": "melonloader:mod"}], "scan_dlls": True, "exclude": []}
        with self.assertRaisesRegex(ValueError, r"no install rule: BepInEx/plugins/Other\.dll"):
            validate_payload(io.BytesIO(archive_bytes({"BepInEx/plugins/Other.dll": b"x"})), install=install)

    def test_excluded_entries_need_no_rule(self) -> None:
        install = {"files": [{"match": "*.dll", "type": "melonloader:mod"}], "scan_dlls": True,
                   "exclude": ["*.txt", "docs/*"]}
        validate_payload(io.BytesIO(archive_bytes({"Mods/a.dll": b"x", "notes.txt": b"y",
                                                   "docs/readme.md": b"z"})), install=install)

    def test_file_rule_matches_by_basename(self) -> None:
        install = {"files": [{"match": "Example.dll", "type": "bepinex:plugin"}], "scan_dlls": True, "exclude": []}
        validate_payload(io.BytesIO(archive_bytes({"nested/dir/Example.dll": b"x"})), install=install)


class RuleDrivenPublicationTests(unittest.TestCase):
    def _publisher(self, directory: str):
        root = Path(directory)
        store = SQLitePackageStore(SQLiteDatabase(root / "access.db"))
        return store, PackagePublisher(store, LocalFileObjectStorage(root / "objects"),
                                       download_base_url="https://server.test")

    def test_rule_driven_version_publishes_without_a_release_file_list(self) -> None:
        with TemporaryDirectory() as directory:
            store, publisher = self._publisher(directory)
            store.create_package("default.example", team_id="default", name="Example",
                                 metadata={"install": FILE_RULES})
            result = publisher.publish_archive(
                package_id="default.example", version="1.0.0",
                archive=archive_bytes({"Example.dll": b"payload"}), now=10, team_id="default",
            )
            release = result.manifest["releases"][0]

        self.assertNotIn("files", release)
        self.assertEqual(release["assets"][0]["name"], "default.example.zip")
        self.assertEqual(result.manifest["install"], {
            "files": FILE_RULES["files"],
            "scan_dlls": True,
            "exclude": [],
        })
        validate(result.manifest, load_schema(ENTRY_SCHEMA), base=SCHEMAS)

    def test_publish_response_install_comes_from_the_package_entity(self) -> None:
        with TemporaryDirectory() as directory:
            store, publisher = self._publisher(directory)
            store.create_package("default.example", team_id="default", name="Example",
                                 metadata={"install": PAYLOAD_RULES})
            # 版本不声明规则：规则只能来自包实体，载荷也必须按实体的规则校验。
            result = publisher.publish_archive(
                package_id="default.example", version="1.0.0",
                archive=archive_bytes({"BepInEx/plugins/Example.dll": b"payload"}), now=10, team_id="default",
            )

        self.assertEqual(result.manifest["install"]["payload"], PAYLOAD_RULES["payload"])
        self.assertNotIn("scan_dlls", result.manifest["install"])
        self.assertNotIn("files", result.manifest["releases"][0])


if __name__ == "__main__":
    unittest.main()
