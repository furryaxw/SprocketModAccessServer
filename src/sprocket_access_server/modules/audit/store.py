from __future__ import annotations

import csv
import io
import json
from typing import Any

from ...infrastructure.database import SQLiteDatabase

SENSITIVE_KEYS = {"token", "access_token", "session_token", "key", "activation_key", "secret", "private_key"}


def redact_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).casefold() in SENSITIVE_KEYS else redact_metadata(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_metadata(item) for item in value]
    return value


class SQLiteAuditStore:
    def __init__(self, database: SQLiteDatabase):
        self.database = database
        self.database.initialize()

    def record(self, *, actor: str, action: str, target: str, metadata: dict[str, Any], now: int,
               team_id: str | None = None) -> None:
        if not actor or not action or not target:
            raise ValueError("audit event identity is required")
        sanitized = redact_metadata(metadata)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_events (actor, action, target, metadata_json, created_at, team_id) VALUES (?, ?, ?, ?, ?, ?)",
                (actor, action, target, json.dumps(sanitized, ensure_ascii=False, sort_keys=True), now, team_id),
            )

    def recent(self, *, limit: int = 100, team_id: str | None = None) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("audit limit is invalid")
        with self.database.transaction() as connection:
            if team_id:
                rows = connection.execute(
                    "SELECT actor, action, target, metadata_json, created_at FROM audit_events WHERE team_id=? ORDER BY id DESC LIMIT ?",
                    (team_id, limit)).fetchall()
            else:
                rows = connection.execute(
                    "SELECT actor, action, target, metadata_json, created_at FROM audit_events ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
        return [
            {
                "actor": row["actor"],
                "action": row["action"],
                "target": row["target"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def search(self, *, actor: str | None = None, action: str | None = None, target: str | None = None,
               query: str | None = None,
               limit: int = 100, offset: int = 0, team_id: str | None = None) -> list[dict[str, Any]]:
        if not 1 <= limit <= 5000 or offset < 0:
            raise ValueError("audit pagination is invalid")
        clauses: list[str] = []
        values: list[object] = []
        if query:
            clauses.append("(actor LIKE ? OR action LIKE ? OR target LIKE ?)")
            values.extend([f"%{query}%"] * 3)
        for field, value in (("actor", actor), ("action", action), ("target", target)):
            if value:
                clauses.append(f"{field} LIKE ?")
                values.append(f"%{value}%")
        if team_id:
            clauses.append("team_id = ?")
            values.append(team_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"SELECT actor, action, target, metadata_json, created_at FROM audit_events{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*values, limit, offset)).fetchall()
        return [{"actor": row["actor"], "action": row["action"], "target": row["target"],
                 "metadata": json.loads(row["metadata_json"]), "created_at": row["created_at"]} for row in rows]

    def count(self, *, actor: str | None = None, action: str | None = None,
              query: str | None = None,
              target: str | None = None, team_id: str | None = None) -> int:
        clauses: list[str] = []
        values: list[object] = []
        if query:
            clauses.append("(actor LIKE ? OR action LIKE ? OR target LIKE ?)")
            values.extend([f"%{query}%"] * 3)
        for field, value in (("actor", actor), ("action", action), ("target", target)):
            if value:
                clauses.append(f"{field} LIKE ?")
                values.append(f"%{value}%")
        if team_id:
            clauses.append("team_id = ?")
            values.append(team_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.database.transaction() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS total FROM audit_events{where}", values,
            ).fetchone()
        return int(row["total"])

    def export_csv(self, *, actor: str | None = None, action: str | None = None, target: str | None = None,
                   limit: int = 5000, team_id: str | None = None) -> str:
        events = self.search(actor=actor, action=action, target=target, limit=limit, offset=0, team_id=team_id)
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(("created_at", "actor", "action", "target", "metadata_json"))
        for event in events:
            writer.writerow((event["created_at"], event["actor"], event["action"], event["target"],
                             json.dumps(event["metadata"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
        return output.getvalue()
