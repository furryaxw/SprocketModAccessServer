from __future__ import annotations

import hashlib
import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.core.ports import GitHubIdentity
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
from sprocket_access_server.modules.permission_templates.store import SQLiteGrantTemplateStore
from sprocket_access_server.modules.system.authentication import AuthenticationService
from sprocket_access_server.modules.system.platform import PlatformStore
from sprocket_access_server.modules.system.provisioning import SystemProvisioning
from tests.support.resource_harness import ResourceDispatchHarness

DLL_PAYLOAD = b"MZ-single-dll-payload"
FILE_RULES = {"files": [{"match": "*.dll", "type": "melonloader:mod"}], "scan_dlls": True, "exclude": []}
ARCHIVE_RULES = {"files": [{"match": "Mods/*.dll", "type": "melonloader:mod"}], "scan_dlls": True, "exclude": []}


class FakeGitHub:
    def verify_access_token(self, _token: str) -> GitHubIdentity:
        return GitHubIdentity("123", "admin")


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


class SingleDllUploadTests(unittest.TestCase):
    def api(self, directory: str) -> ResourceDispatchHarness:
        database = SQLiteDatabase(Path(directory) / "access.db")
        packages = SQLitePackageStore(database)
        storage = LocalFileObjectStorage(Path(directory) / "objects")
        api = ResourceDispatchHarness(
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
        dispatch = api.dispatch

        def dispatch_with_default_team(action: str, node: str, **kwargs):
            headers = dict(kwargs.pop("headers", {}) or {})
            if "Authorization" in headers and "X-Team-Id" not in headers:
                headers["X-Team-Id"] = "default"
            return dispatch(action, node, headers=headers, **kwargs)

        api.dispatch = dispatch_with_default_team
        return api

    def _stage_upload(self, api, directory: str, content: bytes, *, package_id: str, key: str,
                      install: dict | None = None):
        exchanged = api.dispatch("exchange", "system.authentication.github",
                                body=json.dumps({"access_token": "token"}).encode(), now=100)
        headers = {"Authorization": f"Bearer {exchanged.payload['token']}"}
        created = api.dispatch(
            "create", "team.default.package_uploads",
            body=json.dumps({"package_id": package_id, "version": "1.0.0",
                             "size": len(content), "content_type": "application/octet-stream",
                             "metadata": {} if install is None else {"install": install}}).encode(),
            headers={**headers, "Idempotency-Key": f"create-{key}"}, now=100,
        )
        object_key = created.payload["upload"]["object_key"]
        upload_dir = Path(directory) / "objects" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        (upload_dir / object_key).write_bytes(content)
        return headers, created.payload["upload_id"], hashlib.sha256(content).hexdigest()

    def test_single_dll_upload_publishes_a_dll_asset(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            headers, upload_id, digest = self._stage_upload(
                api, directory, DLL_PAYLOAD, package_id="default.example", key="dll-ok", install=FILE_RULES,
            )
            confirmed = api.dispatch(
                "confirm", "team.default.package_uploads",
                data={"upload_id": upload_id, "sha256": digest},
                headers={**headers, "Idempotency-Key": "confirm-dll-ok"}, now=101,
            )

        self.assertEqual(confirmed.status, 201)
        release = confirmed.payload["releases"][0]
        self.assertNotIn("files", release)
        self.assertEqual(release["assets"][0]["name"], "default.example.dll")
        self.assertEqual(confirmed.payload["install"]["files"], FILE_RULES["files"])

    def test_upload_without_install_rules_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            headers, upload_id, digest = self._stage_upload(
                api, directory, DLL_PAYLOAD, package_id="default.example", key="dll-no-rules",
            )
            rejected = api.dispatch(
                "confirm", "team.default.package_uploads",
                data={"upload_id": upload_id, "sha256": digest},
                headers={**headers, "Idempotency-Key": "confirm-dll-no-rules"}, now=101,
            )

        self.assertEqual(rejected.status, 400)
        self.assertEqual(rejected.payload["code"], "invalid_request")
        self.assertIn("must declare install rules", rejected.payload["message"])

    def test_zip_upload_keeps_the_zip_asset(self) -> None:
        with TemporaryDirectory() as directory:
            api = self.api(directory)
            content = _zip_bytes({"Mods/example.dll": b"payload"})
            headers, upload_id, digest = self._stage_upload(
                api, directory, content, package_id="default.example", key="zip-ok", install=ARCHIVE_RULES,
            )
            confirmed = api.dispatch(
                "confirm", "team.default.package_uploads",
                data={"upload_id": upload_id, "sha256": digest},
                headers={**headers, "Idempotency-Key": "confirm-zip-ok"}, now=101,
            )

        self.assertEqual(confirmed.status, 201)
        release = confirmed.payload["releases"][0]
        self.assertNotIn("files", release)
        self.assertEqual(release["assets"][0]["name"], "default.example.zip")


if __name__ == "__main__":
    unittest.main()
