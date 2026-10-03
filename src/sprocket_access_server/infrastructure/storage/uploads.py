from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field

from ..database import SQLiteDatabase
from ...domain.resources import DEFAULT_TEAM_ID, SYSTEM_TEAM_ID, package_resource_node


@dataclass(frozen=True)
class UploadDraft:
    upload_id: str
    object_key: str
    package_id: str
    version: str
    permission_node: str
    size: int
    content_type: str
    status: str
    expires_at: int
    metadata: dict[str, object] = field(default_factory=dict)


class SQLiteUploadStore:
    def __init__(self, database: SQLiteDatabase):
        self.database = database
        self.database.initialize()

    def create(self, *, package_id: str, version: str, size: int, content_type: str,
               metadata: dict[str, object] | None = None, ttl: int = 900, now: int | None = None,
               team_id: str | None = None) -> UploadDraft:
        if not package_id or not version or size < 1 or ttl < 1:
            raise ValueError("upload metadata is invalid")
        timestamp = int(time.time()) if now is None or now <= 0 else now
        owner_team_id = team_id or DEFAULT_TEAM_ID
        if owner_team_id == SYSTEM_TEAM_ID:
            raise ValueError("System workspace cannot publish Packages")
        draft = UploadDraft(secrets.token_urlsafe(18), secrets.token_hex(20), package_id, version,
                            package_resource_node(owner_team_id, package_id),
                            size, content_type or "application/octet-stream", "pending", timestamp + ttl,
                            {} if metadata is None else dict(metadata))
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO upload_drafts (upload_id, object_key, package_id, version, permission_node, size, content_type, metadata_json, status, expires_at, created_at, team_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (draft.upload_id, draft.object_key, draft.package_id, draft.version, draft.permission_node,
                 draft.size, draft.content_type, json.dumps(draft.metadata, ensure_ascii=False), draft.expires_at,
                 timestamp, owner_team_id))
        return draft

    def get_pending(self, upload_id: str, *, now: int | None = None, team_id: str | None = None) -> UploadDraft:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM upload_drafts WHERE upload_id = ? AND (? IS NULL OR team_id = ?)",
                                     (upload_id, team_id, team_id)).fetchone()
        if row is None or row["status"] != "pending" or row["expires_at"] <= timestamp:
            raise ValueError("upload draft is unavailable")
        return UploadDraft(row["upload_id"], row["object_key"], row["package_id"], row["version"],
                           row["permission_node"], row["size"], row["content_type"], row["status"],
                           row["expires_at"], json.loads(row["metadata_json"] or "{}"))

    def mark_confirmed(self, upload_id: str, *, now: int | None = None, team_id: str | None = None) -> None:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE upload_drafts SET status = 'confirmed', confirmed_at = ? WHERE upload_id = ? AND status = 'pending' AND (? IS NULL OR team_id = ?)",
                (timestamp, upload_id, team_id, team_id))
            if cursor.rowcount != 1:
                raise ValueError("upload draft is unavailable")
