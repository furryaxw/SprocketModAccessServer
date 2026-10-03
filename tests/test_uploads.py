from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.storage.uploads import SQLiteUploadStore


class UploadStoreTests(unittest.TestCase):
    def test_expired_draft_cannot_be_confirmed(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteUploadStore(SQLiteDatabase(Path(directory) / "access.db"))
            draft = store.create(package_id="default.example", version="1.0.0", size=1,
                                 content_type="application/zip", ttl=10, now=100)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                store.get_pending(draft.upload_id, now=110)

    def test_confirmation_is_one_time(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteUploadStore(SQLiteDatabase(Path(directory) / "access.db"))
            draft = store.create(package_id="default.example", version="1.0.0", size=1,
                                 content_type="application/zip", now=100)
            store.mark_confirmed(draft.upload_id, now=101)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                store.mark_confirmed(draft.upload_id, now=102)


if __name__ == "__main__":
    unittest.main()
