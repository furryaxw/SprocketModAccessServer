from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.infrastructure.security.sessions import SQLiteSessionStore


class SessionStoreTests(unittest.TestCase):
    def test_token_is_authenticated_until_expiry_then_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteSessionStore(SQLiteDatabase(Path(directory) / "access.db"), b"pepper")
            session = store.create("123", ttl=10, now=100)
            self.assertEqual(store.authenticate(str(session["token"]), now=109), "123")
            with self.assertRaisesRegex(ValueError, "invalid or expired"):
                store.authenticate(str(session["token"]), now=110)

    def test_revoked_token_is_rejected_and_plaintext_is_not_stored(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            store = SQLiteSessionStore(database, b"pepper")
            session = store.create("123", ttl=10, now=100)
            token = str(session["token"])
            store.revoke(token, now=101)
            with self.assertRaisesRegex(ValueError, "invalid or expired"):
                store.authenticate(token, now=101)
            with database.transaction() as connection:
                stored = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
            self.assertNotEqual(stored, token)


if __name__ == "__main__":
    unittest.main()
