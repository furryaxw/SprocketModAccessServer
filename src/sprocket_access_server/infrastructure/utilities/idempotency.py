from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from ..database import SQLiteDatabase
from ...domain.errors import ApiError


class SQLiteIdempotencyStore:
    def __init__(self, database: SQLiteDatabase):
        self.database = database
        self.database.initialize()

    def run(
            self,
            *,
            scope: str,
            request_key: str,
            request: dict[str, Any],
            operation: Callable[[], dict[str, Any]],
            now: int | None = None,
    ) -> dict[str, Any]:
        if not scope or not request_key or len(request_key) > 256:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is invalid")
        request_hash = hashlib.sha256(
            json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT request_hash, response_json FROM idempotency_records WHERE scope = ? AND request_key = ?",
                (scope, request_key),
            ).fetchone()
            if row is not None:
                if row["request_hash"] != request_hash:
                    raise ApiError(409, "idempotency_key_reused", "Idempotency-Key was reused for a different request")
                return json.loads(row["response_json"])
            result = operation()
            connection.execute(
                "INSERT INTO idempotency_records (scope, request_key, request_hash, response_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (scope, request_key, request_hash, json.dumps(result, ensure_ascii=False, sort_keys=True), timestamp),
            )
            return result
