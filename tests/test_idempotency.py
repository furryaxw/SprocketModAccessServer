from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.domain.errors import ApiError
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.utilities.idempotency import SQLiteIdempotencyStore


class IdempotencyTests(unittest.TestCase):
    def test_same_request_replays_without_running_operation_again(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteIdempotencyStore(SQLiteDatabase(Path(directory) / "access.db"))
            calls = []
            operation = lambda: calls.append(1) or {"batch_id": "b1"}
            first = store.run(scope="admin", request_key="k", request={"q": 1}, operation=operation, now=1)
            second = store.run(scope="admin", request_key="k", request={"q": 1}, operation=operation, now=2)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_reusing_key_for_different_request_is_conflict(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteIdempotencyStore(SQLiteDatabase(Path(directory) / "access.db"))
            store.run(scope="admin", request_key="k", request={"q": 1}, operation=lambda: {"ok": True}, now=1)
            with self.assertRaises(ApiError) as raised:
                store.run(scope="admin", request_key="k", request={"q": 2}, operation=lambda: {"ok": True}, now=1)
        self.assertEqual(raised.exception.code, "idempotency_key_reused")


if __name__ == "__main__":
    unittest.main()
