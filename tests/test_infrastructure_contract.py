from __future__ import annotations

import hashlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.infrastructure.configuration.settings import ServerSettings
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.storage.objects import LocalFileObjectStorage


class InfrastructureContractTests(unittest.TestCase):
    def test_defaults_are_sqlite_and_local_file(self) -> None:
        with TemporaryDirectory() as directory:
            old_database = os.environ.pop("SMAS_DATABASE_URL", None)
            old_storage = os.environ.pop("SMAS_OBJECT_STORAGE_URL", None)
            try:
                settings = ServerSettings.from_environment()
            finally:
                if old_database is not None:
                    os.environ["SMAS_DATABASE_URL"] = old_database
                if old_storage is not None:
                    os.environ["SMAS_OBJECT_STORAGE_URL"] = old_storage
        self.assertEqual(settings.sqlite_path(Path(directory)).name, "access.db")
        self.assertEqual(settings.local_storage_path(Path(directory)).name, "packages")

    def test_sqlite_schema_is_created_idempotently(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "data" / "access.db")
            database.initialize()
            database.initialize()
            with database.transaction() as connection:
                names = {
                    row["name"]
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
        self.assertIn("users", names)
        self.assertIn("sessions", names)
        self.assertIn("idempotency_records", names)

    def test_local_storage_is_content_addressed_and_atomic(self) -> None:
        payload = b"package bytes"
        digest = hashlib.sha256(payload).hexdigest()
        with TemporaryDirectory() as directory:
            storage = LocalFileObjectStorage(Path(directory) / "objects")
            storage.put(io.BytesIO(payload), digest=digest, size=len(payload))
            self.assertTrue(storage.exists(digest))
            with storage.open(digest) as source:
                self.assertEqual(source.read(), payload)
            with self.assertRaises(ValueError):
                storage.put(io.BytesIO(b"tampered"), digest=digest, size=len(payload))


if __name__ == "__main__":
    unittest.main()
