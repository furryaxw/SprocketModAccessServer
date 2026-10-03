from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

from ..database import SQLiteDatabase

logger = logging.getLogger(__name__)


class SQLiteSessionStore:
    def __init__(self, database: SQLiteDatabase, pepper: bytes):
        if not pepper:
            raise ValueError("session pepper is required")
        self.database = database
        self.pepper = bytes(pepper)
        self.database.initialize()

    def _hash(self, token: str) -> str:
        if not token or len(token) > 512:
            raise ValueError("session token is invalid")
        return hmac.new(self.pepper, token.encode("utf-8"), hashlib.sha256).hexdigest()

    def create(self, github_user_id: str, *, ttl: int, now: int | None = None) -> dict[str, int | str]:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if not github_user_id or ttl < 1:
            raise ValueError("session identity and TTL are required")
        token = secrets.token_urlsafe(32)
        expires_at = timestamp + ttl
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO users (github_user_id, created_at, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(github_user_id) DO UPDATE SET updated_at = excluded.updated_at""",
                (github_user_id, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO sessions (token_hash, github_user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (self._hash(token), github_user_id, timestamp, expires_at),
            )
        logger.debug("session created user_id=%s created_at=%d expires_at=%d", github_user_id, timestamp, expires_at)
        return {"token": token, "github_user_id": github_user_id, "created_at": timestamp, "expires_at": expires_at}

    def authenticate(self, token: str, *, now: int | None = None) -> str:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        token_hash = self._hash(token)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT github_user_id, expires_at, revoked_at FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None or row["revoked_at"] is not None or row["expires_at"] <= timestamp:
            logger.debug("session authentication rejected token_present=%s token_length=%d", bool(token), len(token))
            raise ValueError("session token is invalid or expired")
        logger.debug("session authenticated user_id=%s", row["github_user_id"])
        return str(row["github_user_id"])

    def revoke(self, token: str, *, now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        token_hash = self._hash(token)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (timestamp, token_hash),
            )
        logger.debug("session revoke attempted token_present=%s changed=%d", bool(token), cursor.rowcount)
