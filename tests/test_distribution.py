from __future__ import annotations

import hashlib
import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.modules.packages.files import PAYLOAD_DLL, PAYLOAD_ZIP
from sprocket_access_server.modules.packages.publication import PackagePublisher
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.modules.packages.store import SQLitePackageStore
from sprocket_access_server.infrastructure.storage.objects import LocalFileObjectStorage

# 落点由包声明的规则描述：`match` 覆盖归档条目，`type` 交给客户端解析。
FILE_RULES = {"files": [{"match": "*.dll", "type": "melonloader:mod"}], "scan_dlls": True, "exclude": []}
# 只覆盖 Mods 下的条目：用来验证未被规则覆盖的归档条目被拒绝。
MODS_RULES = {"files": [{"match": "Mods/*.dll", "type": "melonloader:mod"}], "scan_dlls": True, "exclude": []}


def archive_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


class DistributionTests(unittest.TestCase):
    def publisher(self, root: Path) -> PackagePublisher:
        return PackagePublisher(SQLitePackageStore(SQLiteDatabase(root / "access.db")),
                                LocalFileObjectStorage(root / "objects"))

    def test_publish_stores_bytes_and_the_release_shape(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            publisher = self.publisher(root)
            archive = archive_bytes({"Mods/example.dll": b"payload"})
            result = publisher.publish_archive(
                package_id="default.example", version="1.0.0", archive=archive,
                metadata={"install": FILE_RULES}, now=10, team_id="default",
            )
            digest = hashlib.sha256(archive).hexdigest()
            self.assertEqual(result.package.archive_digest, digest)
            self.assertTrue((root / "objects" / digest).is_file())
            release = result.manifest["releases"][0]
            self.assertEqual(release["assets"][0]["name"], "default.example.zip")
            self.assertEqual(release["dependencies"], [])
            # 归档内容不出现在条目里：落点由客户端按包规则解析。
            self.assertNotIn("files", release)
            self.assertNotIn("repository", result.manifest)

    def test_archive_entry_without_a_rule_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            publisher = self.publisher(Path(directory))
            archive = archive_bytes({"BepInEx/plugins/example.dll": b"payload"})
            with self.assertRaisesRegex(ValueError, "no install rule"):
                publisher.publish_archive(package_id="default.example", version="1.0.0", archive=archive,
                                          metadata={"install": MODS_RULES}, team_id="default")

    def test_publish_without_install_rules_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            publisher = self.publisher(Path(directory))
            with self.assertRaisesRegex(ValueError, "must declare install rules"):
                publisher.publish_archive(package_id="default.example", version="1.0.0",
                                          archive=archive_bytes({"Mods/example.dll": b"payload"}),
                                          team_id="default")

    def test_empty_archive_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            publisher = self.publisher(Path(directory))
            with self.assertRaisesRegex(ValueError, "empty"):
                publisher.publish_archive(package_id="default.example", version="1.0.0", archive=b"")

    def test_archive_asset_keeps_the_zip_extension(self) -> None:
        with TemporaryDirectory() as directory:
            publisher = self.publisher(Path(directory))
            # 归档里只有一个 DLL 也不能因此命名成 .dll：载荷是 zip，客户端会按 .zip 解包。
            result = publisher.publish_archive(
                package_id="default.example", version="1.0.0",
                archive=archive_bytes({"Mods/example.dll": b"payload"}),
                metadata={"install": FILE_RULES}, now=10, team_id="default",
            )
            self.assertEqual(result.manifest["releases"][0]["assets"][0]["name"], "default.example.zip")
            self.assertEqual(result.package.payload_kind, PAYLOAD_ZIP)

    def test_single_dll_publishes_a_dll_asset(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLitePackageStore(SQLiteDatabase(root / "access.db"))
            storage = LocalFileObjectStorage(root / "objects")
            publisher = PackagePublisher(store, storage)
            payload = b"MZ-single-dll-payload"
            digest = hashlib.sha256(payload).hexdigest()
            storage.put(io.BytesIO(payload), digest=digest, size=len(payload))

            result = publisher.register_existing(
                package_id="default.example", version="1.0.0", digest=digest, size=len(payload),
                metadata={"install": FILE_RULES}, now=10, team_id="default",
            )

            self.assertEqual(result.package.payload_kind, PAYLOAD_DLL)
            release = result.manifest["releases"][0]
            self.assertEqual(release["assets"][0]["name"], "default.example.dll")
            self.assertNotIn("files", release)
            # 载荷形态记在版本行里，重新读出后资产名仍然正确。
            stored = store.get("default.example", "1.0.0", team_id="default")
            self.assertEqual(stored.release()["assets"][0]["name"], "default.example.dll")


if __name__ == "__main__":
    unittest.main()
