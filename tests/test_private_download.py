from __future__ import annotations

import hashlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sprocket_access_server.core.ports import GitHubIdentity
from sprocket_access_server.domain.protocol import ServerInfo
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.network import ResourceRegistry
from sprocket_access_server.infrastructure.security.sessions import SQLiteSessionStore
from sprocket_access_server.infrastructure.storage.objects import LocalFileObjectStorage
from sprocket_access_server.modules.packages.store import PackageVersion, SQLitePackageStore
from sprocket_access_server.modules.system.authentication import AuthenticationService
from sprocket_access_server.presentation.asgi import create_app
from tests.support.v1_http_client import call, json_call

INSTALL = {"files": [{"match": "*.dll", "type": "melonloader:mod"}], "scan_dlls": True, "exclude": []}
SNAPSHOT = {"install": INSTALL}
ARCHIVE = b"PK\x03\x04-archive-bytes"
PACKAGE = "default.example"


class FakeGitHub:
    def verify_access_token(self, _token: str) -> GitHubIdentity:
        return GitHubIdentity("123", "admin")


class FakeAuthorization:
    def allows(self, _user_id: str, _permission: str, *, now=None, team_id=None) -> bool:
        return True


class RedirectingStorage:
    """对象存储的替身：下载改成桶的签名 URL。"""

    download_origin = "https://storage.test"

    def download_redirect(self, digest: str) -> str:
        return f"https://storage.test/mods/{digest}?X-Amz-Signature=stub"

    def open(self, digest: str):
        raise AssertionError("redirect-capable storage must not stream through the server")


class PrivateDownloadTests(unittest.TestCase):
    def _app(self, directory: str, storage, *, download_origins: tuple[str, ...] = ()):
        database = SQLiteDatabase(Path(directory) / "access.db")
        sessions = SQLiteSessionStore(database, b"pepper")
        session = sessions.create("123", ttl=10 ** 9)
        store = SQLitePackageStore(database)
        store.create_package(PACKAGE, team_id="default", name="Example", metadata={"install": INSTALL})
        digest = hashlib.sha256(ARCHIVE).hexdigest()
        store.publish(
            PackageVersion(PACKAGE, "1.0.0", "team.default.example", digest, len(ARCHIVE), "published", 5,
                           SNAPSHOT),
            now=5, team_id="default",
        )
        context = SimpleNamespace(
            resources=ResourceRegistry(),
            service=lambda name: {
                "authentication": AuthenticationService(FakeGitHub(), sessions),
                "authorization_service": FakeAuthorization(),
                "packages": store,
                "server_info": ServerInfo("server-1", "Test", None, {}, download_origins),
                "direct_storage": storage,
            }[name],
        )
        app = create_app(ServerInfo("server-1", "Test", None, {}, download_origins),
                         context.service("authentication"), module_context=context)
        return app, session["token"], digest

    def test_local_storage_streams_the_archive(self) -> None:
        with TemporaryDirectory() as directory:
            storage = LocalFileObjectStorage(Path(directory) / "objects")
            app, token, digest = self._app(directory, storage)
            storage.put(io.BytesIO(ARCHIVE), digest=digest, size=len(ARCHIVE))
            status, headers, payload = call(
                app, "GET", f"/v1/packages/{PACKAGE}/download", query="version=1.0.0",
                headers={"authorization": f"Bearer {token}", "host": "server.test"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, ARCHIVE)
        self.assertEqual(headers["content-length"], str(len(ARCHIVE)))
        self.assertEqual(headers["cache-control"], "no-store")

    def test_object_storage_redirects_to_the_signed_url(self) -> None:
        with TemporaryDirectory() as directory:
            app, token, digest = self._app(directory, RedirectingStorage(),
                                           download_origins=("https://storage.test",))
            status, headers, payload = call(
                app, "GET", f"/v1/packages/{PACKAGE}/download", query="version=1.0.0",
                headers={"authorization": f"Bearer {token}", "host": "server.test"},
            )

        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], f"https://storage.test/mods/{digest}?X-Amz-Signature=stub")
        self.assertEqual(payload, b"")

    def test_download_requires_a_session(self) -> None:
        with TemporaryDirectory() as directory:
            app, _token, _digest = self._app(directory, RedirectingStorage())
            status, payload = json_call(
                app, "GET", f"/v1/packages/{PACKAGE}/download", query="version=1.0.0",
                headers={"host": "server.test"},
            )

        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "invalid_session")

    def test_download_requires_a_version(self) -> None:
        with TemporaryDirectory() as directory:
            app, token, _digest = self._app(directory, RedirectingStorage())
            status, payload = json_call(
                app, "GET", f"/v1/packages/{PACKAGE}/download",
                headers={"authorization": f"Bearer {token}", "host": "server.test"},
            )

        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "invalid_request")

    def test_server_info_declares_the_download_origins(self) -> None:
        with TemporaryDirectory() as directory:
            app, _token, _digest = self._app(directory, RedirectingStorage(),
                                             download_origins=("https://storage.test", "cdn.example.test"))
            status, payload = json_call(app, "GET", "/v1/server-info", headers={"host": "server.test"})

        self.assertEqual(status, 200)
        self.assertEqual(payload["download_origins"], ["https://storage.test", "cdn.example.test"])
        self.assertEqual(json.loads(json.dumps(payload))["protocol_version"], 2)


if __name__ == "__main__":
    unittest.main()
