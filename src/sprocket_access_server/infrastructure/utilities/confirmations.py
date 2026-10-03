from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from ..database import SQLiteDatabase


@dataclass(frozen=True)
class Confirmation:
    action: str
    target: str
    expires_at: int


class SQLiteConfirmationStore:
    def __init__(self, database: SQLiteDatabase, *, ttl: int = 300):
        if ttl < 1 or ttl > 3600:
            raise ValueError("confirmation token TTL is invalid")
        self.database = database
        self.ttl = ttl
        self.database.initialize()

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue(self, *, action: str, target: str, created_by: str, now: int | None = None) -> tuple[str, Confirmation]:
        if not action or not target or not created_by:
            raise ValueError("confirmation request is invalid")
        timestamp = int(time.time()) if now is None or now <= 0 else now
        token = secrets.token_urlsafe(32)
        confirmation = Confirmation(action, target, timestamp + self.ttl)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO confirmation_tokens (token_hash, action, target, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (self._hash(token), action, target, created_by, timestamp, confirmation.expires_at),
            )
        return token, confirmation

    def consume(self, token: str, *, action: str, target: str, now: int | None = None) -> Confirmation:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if not token:
            raise ValueError("confirmation token is required")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM confirmation_tokens WHERE token_hash = ?",
                                     (self._hash(token),)).fetchone()
            if row is None or row["action"] != action or row["target"] != target or row["used_at"] is not None or row[
                "expires_at"] <= timestamp:
                raise ValueError("confirmation token is invalid or expired")
            connection.execute("UPDATE confirmation_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                               (timestamp, self._hash(token)))
        return Confirmation(row["action"], row["target"], row["expires_at"])
