from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.modules.audit.store import SQLiteAuditStore
from sprocket_access_server.infrastructure.database import SQLiteDatabase


class AuditStoreTests(unittest.TestCase):
    def test_sensitive_metadata_is_redacted(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteAuditStore(SQLiteDatabase(Path(directory) / "access.db"))
            store.record(
                actor="admin:123", action="key.redeem", target="grant-1",
                metadata={"access_token": "secret", "nested": {"key": "plain", "count": 1}}, now=10,
            )
            event = store.recent()[0]
        self.assertEqual(event["metadata"]["access_token"], "[REDACTED]")
        self.assertEqual(event["metadata"]["nested"]["key"], "[REDACTED]")
        self.assertEqual(event["metadata"]["nested"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
