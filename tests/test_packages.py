from __future__ import annotations

import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.modules.packages.files import (
    PAYLOAD_DLL,
    PAYLOAD_ZIP,
    payload_kind,
)
from sprocket_access_server.modules.packages.resources import _package_update_preview
from sprocket_access_server.modules.packages.store import PackageVersion, SQLitePackageStore


INSTALL = {"files": [{"match": "*.dll", "type": "melonloader:mod"}], "scan_dlls": True, "exclude": []}
# 版本行记录包元数据的快照，因此同样带规则。
SNAPSHOT = {"install": INSTALL}


def archive_with(entries: dict[str, bytes]) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    buffer.seek(0)
    return buffer


class PayloadKindTests(unittest.TestCase):
    def test_payload_kind_follows_the_magic_number(self) -> None:
        self.assertEqual(payload_kind(archive_with({"Mods/example.dll": b"a"})), PAYLOAD_ZIP)
        self.assertEqual(payload_kind(io.BytesIO(b"MZ\x90\x00")), PAYLOAD_DLL)


class PackageStoreTests(unittest.TestCase):
    def test_manifest_matches_the_public_v3_entry_shape(self) -> None:
        package = PackageVersion(
            "default.example", "1.0.0", "team.default.example", "a" * 64, 10, "published", 1,
            {"name": "Example", "authors": ["alice"], "repository": "alice/example"},
            "default",
        )
        entry = package.manifest(download_base_url="https://server.test")
        release = entry["releases"][0]
        # 条目直接是公开 v3 形状。
        self.assertEqual(entry["schema_version"], 3)
        # 顶层没有 published_at；时间在 release 里且为 ISO 字符串。
        self.assertNotIn("published_at", entry)
        self.assertTrue(str(release["published_at"]).endswith("Z"))
        # 资产是公开形状；归档内容不进条目。
        self.assertEqual(release["assets"][0]["id"], 1)
        self.assertEqual(release["assets"][0]["digest"], f"sha256:{'a' * 64}")
        self.assertEqual(release["assets"][0]["download_url"],
                         "https://server.test/v1/packages/default.example/download")
        self.assertEqual(release["compatibility"], {"source": "declared"})
        self.assertNotIn("files", release)
        self.assertEqual(entry["repository"], "alice/example")
        # 抓取规则、目录与加载器元数据仍不出现，`status`/`metadata` 也已去掉。
        for absent in ("release", "featured", "meta_url", "kind", "provides", "supply",
                       "status", "metadata", "required_permission"):
            self.assertNotIn(absent, entry)
        self.assertEqual(entry["name"], "Example")

    def test_manifest_omits_an_unknown_repository(self) -> None:
        package = PackageVersion(
            "default.example", "1.0.0", "team.default.example", "a" * 64, 10, "published", 1,
            {"name": "Example", "repository": "alice"}, "default",
        )
        entry = package.manifest(download_base_url="https://server.test")
        self.assertNotIn("repository", entry)

    def test_manifest_omits_authors_when_there_are_none(self) -> None:
        package = PackageVersion(
            "default.example", "1.0.0", "team.default.example", "a" * 64, 10, "published", 1,
            {"name": "Example"}, "default",
        )
        entry = package.manifest(download_base_url="https://server.test")
        self.assertNotIn("authors", entry)

    def test_manifest_trims_a_declared_repository(self) -> None:
        package = PackageVersion(
            "default.example", "1.0.0", "team.default.example", "a" * 64, 10, "published", 1,
            {"name": "Example", "repository": " alice/example "}, "default",
        )
        entry = package.manifest(download_base_url="https://server.test")
        self.assertEqual(entry["repository"], "alice/example")

    def test_manifest_can_be_signed_without_mutating_package_record(self) -> None:
        package = PackageVersion("default.example", "1.0.0", "team.default.example", "a" * 64, 10, "published", 1)
        signed = package.signed_manifest(Ed25519PrivateKey.generate(), "key-1")
        self.assertEqual(signed["id"], package.package_id)
        self.assertIn("signature", signed)

    def test_package_entity_lifecycle_and_index_entry(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLitePackageStore(SQLiteDatabase(Path(directory) / "access.db"))
            with self.assertRaisesRegex(ValueError, "Team id"):
                store.create_package("other.example", team_id="default", name="Example", metadata=dict(SNAPSHOT))
            created = store.create_package("default.example", team_id="default", name="Example",
                                            metadata={"license": "MIT", **SNAPSHOT})
            self.assertEqual(created.versions, ())
            with self.assertRaisesRegex(ValueError, "already exists"):
                store.create_package("default.example", team_id="default", name="Example", metadata=dict(SNAPSHOT))
            # 还没有已发布版本时，包不进索引。
            self.assertEqual(store.records(team_ids=("default",)), ())
            store.publish(
                PackageVersion(
                    "default.example", "1.0.0", "team.default.example", "a" * 64, 10, "published", 5,
                    SNAPSHOT,
                ),
                now=5, team_id="default",
            )
            records = store.records(team_ids=("default",))
            self.assertEqual(len(records), 1)
            entry = records[0].entry()
            self.assertEqual(entry["id"], "default.example")
            self.assertEqual(len(entry["releases"]), 1)
            self.assertNotIn("files", entry["releases"][0])
            # 元数据与状态来自包实体。
            self.assertEqual(entry["license"], "MIT")
            self.assertEqual(store.update_package("default.example", team_id="default", status="disabled").status,
                             "disabled")
            failed, total = store.search_records(team_id="default")
            self.assertEqual((len(failed), total), (1, 1))
            self.assertEqual(len(failed[0].versions), 1)

    def test_package_versions_are_immutable_and_status_is_separate(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLitePackageStore(SQLiteDatabase(Path(directory) / "access.db"))
            package = PackageVersion("default.example", "1.0.0", "team.default.packages.default_example",
                                     "a" * 64, 10, "published", 1, SNAPSHOT)
            store.publish(package, now=1)
            with self.assertRaisesRegex(ValueError, "already exists"):
                store.publish(package, now=2)
            store.set_status(package.package_id, package.version, "disabled")
            self.assertEqual(store.get(package.package_id, package.version).status, "disabled")
            # 条目只发布已发布版本，`status` 不再出现在条目里。
            self.assertNotIn("status", store.get(package.package_id, package.version).manifest())

    def test_metadata_can_change_without_replacing_immutable_archive(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLitePackageStore(SQLiteDatabase(Path(directory) / "access.db"))
            package = PackageVersion("default.example", "1.0.0", "team.default.packages.default_example",
                                     "a" * 64, 10, "published", 1, SNAPSHOT)
            store.publish(package, now=1)
            # 版本元数据是整体替换：提交什么就存什么（保留不可变的归档身份与载荷形态）。
            store.update_metadata(package.package_id, package.version,
                                  {"install": INSTALL, "channel": "beta", "display_name": {"en": "Beta"}})
            updated = store.get(package.package_id, package.version)
            self.assertEqual(updated.metadata["channel"], "beta")
            self.assertEqual(sorted(updated.metadata), ["channel", "display_name", "install"])
            self.assertEqual(updated.archive_digest, package.archive_digest)
            self.assertEqual(updated.archive_size, package.archive_size)

    def test_metadata_replacement_must_stay_publishable(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLitePackageStore(SQLiteDatabase(Path(directory) / "access.db"))
            package = PackageVersion("default.example", "1.0.0", "team.default.default_example",
                                     "a" * 64, 10, "published", 1, SNAPSHOT)
            store.publish(package, now=1)
            for metadata in ({}, {"channel": "beta"}):
                with self.subTest(metadata=metadata):
                    with self.assertRaises(ValueError):
                        store.update_metadata(package.package_id, package.version, metadata)

    def test_metadata_cannot_replace_identity_or_archive_fields(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLitePackageStore(SQLiteDatabase(Path(directory) / "access.db"))
            package = PackageVersion("default.example", "1.0.0", "team.default.default_example",
                                     "a" * 64, 10, "published", 1, SNAPSHOT)
            store.publish(package, now=1)
            for field in ("package_id", "version", "digest", "sha256", "archive_digest", "archive_size", "size"):
                with self.subTest(field=field):
                    with self.assertRaisesRegex(ValueError, "immutable"):
                        store.update_metadata(package.package_id, package.version, {field: "changed"})

    def test_invalid_package_identity_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLitePackageStore(SQLiteDatabase(Path(directory) / "access.db"))
            package = PackageVersion("Bad", "1.0", "team.default.packages.bad", "a" * 64, 10, "published", 1)
            with self.assertRaisesRegex(ValueError, "identity"):
                store.publish(package)

    def test_package_identity_requires_the_owning_team_prefix(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLitePackageStore(SQLiteDatabase(Path(directory) / "access.db"))
            with self.assertRaisesRegex(ValueError, "identity"):
                store.publish(PackageVersion("sprocketthermal", "1.0.0", "team.default.packages.example",
                                             "a" * 64, 10, "published", 1), now=1)
            with self.assertRaisesRegex(ValueError, "Team id"):
                store.publish(PackageVersion("other.example", "1.0.0", "team.default.packages.example",
                                             "a" * 64, 10, "published", 1), now=1)
            owned = PackageVersion("default.sprocketthermal", "1.0.0", "team.default.sprocketthermal",
                                   "a" * 64, 10, "published", 1, SNAPSHOT)
            store.publish(owned, now=1)
            self.assertEqual(store.get("default.sprocketthermal", "1.0.0").package_id, "default.sprocketthermal")

    def test_package_update_preview_reports_status_and_immutable_fields(self) -> None:
        package = PackageVersion(
            "default.example", "1.0.0", "team.default.default_example", "a" * 64, 10, "published", 1,
            {"channel": "stable"},
        )
        preview = _package_update_preview(package, {"channel": "beta", "id": "other", "sha256": "b" * 64}, "disabled")
        self.assertEqual(preview["diff"]["metadata_changed"], ["channel"])
        self.assertEqual(preview["diff"]["immutable_fields"], ["id", "sha256"])
        self.assertTrue(preview["diff"]["status_changed"])
        self.assertTrue(preview["requires_confirmation"])


if __name__ == "__main__":
    unittest.main()
