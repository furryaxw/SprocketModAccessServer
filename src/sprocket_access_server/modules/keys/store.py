from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from ..audit.store import redact_metadata
from ..permission_assignments.store import (
    SQLiteAuthorizationStore,
    assignments_for_permissions,
    public_permissions,
    serialize_assignments,
)
from ...domain.models import PermissionAssignment
from ...infrastructure.database import SQLiteDatabase
from ...infrastructure.events import ResourceChanged


@dataclass(frozen=True)
class IssuedKey:
    key_id: str
    plaintext: str
    expires_at: int | None
    batch_id: str = ""


@dataclass(frozen=True)
class KeySummary:
    key_id: str
    key_prefix: str
    status: str
    note: str
    permissions: tuple[str, ...]
    assignments: tuple[dict[str, object], ...]
    expires_at: int | None
    redeemed_github_user_id: str | None
    redeemed_at: int | None
    created_at: int
    team_id: str | None = None
    # 明文随库存保存，因此有 keys.read 的账户可以再次读取（不再是一次性返回）。
    plaintext: str = ""
    batch_id: str = ""
    # 发放记录：码取出来交给谁、什么时候。与兑换（redeemed_*）是两件事。
    delivered_to: str | None = None
    delivered_at: int | None = None


_KEY_COLUMNS = (
    "key_id, batch_id, COALESCE(key_prefix, 'LEGACY') AS key_prefix, key_plaintext, status, note, "
    "permissions_json, assignments_json, expires_at, redeemed_github_user_id, redeemed_at, created_at, team_id, "
    "delivered_to, delivered_at"
)


def _summary(row) -> KeySummary:
    return KeySummary(
        row["key_id"],
        row["key_prefix"],
        row["status"],
        row["note"],
        tuple(json.loads(row["permissions_json"])),
        tuple(json.loads(row["assignments_json"] or "[]")),
        row["expires_at"],
        row["redeemed_github_user_id"],
        row["redeemed_at"],
        row["created_at"],
        row["team_id"],
        row["key_plaintext"] or "",
        row["batch_id"] or "",
        row["delivered_to"],
        row["delivered_at"],
    )


class KeyIssuer:
    _KEY_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    def __init__(self, database: SQLiteDatabase, authorization: SQLiteAuthorizationStore, pepper: bytes, *,
                 events=None):
        if not pepper:
            raise ValueError("key pepper is required")
        self.database = database
        self.authorization = authorization
        self.pepper = bytes(pepper)
        self.events = events
        self.database.initialize()

    def _hash(self, plaintext: str) -> str:
        return hmac.new(self.pepper, ("key:" + plaintext).encode("utf-8"), hashlib.sha256).hexdigest()

    def hash_key(self, plaintext: str) -> str:
        if not plaintext or len(plaintext) > 512:
            raise ValueError("activation key is invalid")
        return self._hash(plaintext.strip())

    def team_for_key(self, key_hash: str) -> str | None:
        """Key 所属 Team：`key_hash` 唯一，兑换由 Key 自身决定 Team 上下文。"""
        if not key_hash:
            return None
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT team_id FROM activation_keys WHERE key_hash=?", (key_hash,)
            ).fetchone()
        return None if row is None else row["team_id"]

    @classmethod
    def _new_plaintext(cls) -> str:
        # Crockford alphabet; the prefix is fixed and the body is 20 symbols.
        return "SMAS" + "".join(secrets.choice(cls._KEY_ALPHABET) for _ in range(20))

    def create_batch(
            self,
            *,
            quantity: int,
            permissions: frozenset[str],
            expires_at: int | None,
            actor: str,
            team_id: str | None = None,
            assignments: tuple[PermissionAssignment, ...] | None = None,
            template_id: str | None = None,
            key_kind: str = "snapshot",
            now: int | None = None,
    ) -> tuple[IssuedKey, ...]:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if quantity < 1 or quantity > 10_000 or not permissions or not actor:
            raise ValueError("key batch request is invalid")
        if key_kind not in {"snapshot", "template"}:
            raise ValueError("key kind is invalid")
        batch_id = secrets.token_hex(12)
        issued: list[IssuedKey] = []
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO key_batches (batch_id, name, quantity, permissions_json, assignments_json, template_id, expires_at, created_by, created_at, team_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    batch_id,
                    "batch-" + batch_id,
                    quantity,
                    json.dumps(sorted(permissions)),
                    serialize_assignments(assignments or assignments_for_permissions(
                        permissions, source_type="key_batch", source_id=batch_id)),
                    template_id,
                    expires_at,
                    actor,
                    timestamp,
                    team_id,
                ),
            )
            for _ in range(quantity):
                key_id = secrets.token_hex(16)
                plaintext = self._new_plaintext()
                key_assignments = assignments or assignments_for_permissions(
                    permissions, source_type="key", source_id=key_id
                )
                key_permissions = public_permissions(key_assignments)
                connection.execute(
                    "INSERT INTO activation_keys (key_id, batch_id, key_hash, key_plaintext, key_prefix, permissions_json, assignments_json, template_id, key_kind, status, expires_at, created_at, team_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unused', ?, ?, ?)",
                    (key_id, batch_id, self._hash(plaintext), plaintext, plaintext[:10],
                     json.dumps(sorted(key_permissions)),
                     serialize_assignments(key_assignments), template_id, key_kind, expires_at, timestamp, team_id),
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO permission_nodes(node) VALUES (?)",
                    [(item.node,) for item in key_assignments],
                )
                issued.append(IssuedKey(key_id, plaintext, expires_at, batch_id))
        self._publish_change(
            kind="create",
            action="distribute",
            team_id=team_id,
            data={"batch_id": batch_id, "quantity": quantity},
        )
        return tuple(issued)

    def batch_stats(self, batch_id: str, *, team_id: str | None = None) -> dict[str, object]:
        if not batch_id or len(batch_id) > 64:
            raise ValueError("batch id is invalid")
        with self.database.transaction() as connection:
            batch = connection.execute(
                "SELECT batch_id, name, quantity, permissions_json, expires_at, created_by, created_at FROM key_batches WHERE batch_id = ? AND (? IS NULL OR team_id = ?)",
                (batch_id, team_id, team_id)).fetchone()
            if batch is None:
                raise ValueError("key batch was not found")
            counts = {row["status"]: int(row["count"]) for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM activation_keys WHERE batch_id = ? GROUP BY status",
                (batch_id,)).fetchall()}
        return {"batch_id": batch["batch_id"], "name": batch["name"], "quantity": batch["quantity"],
                "permissions": json.loads(batch["permissions_json"]), "expires_at": batch["expires_at"],
                "created_by": batch["created_by"], "created_at": batch["created_at"], "counts": counts}

    def search(self, *, status: str | None = None, prefix: str = "", batch_id: str | None = None,
               delivered: bool | None = None, limit: int = 50,
               offset: int = 0, team_id: str | None = None) -> tuple[
        tuple[KeySummary, ...], dict[str, int], int]:
        if status is not None and status not in {"unused", "redeemed", "revoked"}:
            raise ValueError("key status is invalid")
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("key pagination is invalid")
        prefix = prefix.strip().upper()
        with self.database.transaction() as connection:
            clauses: list[str] = []
            values: list[object] = []
            if status:
                clauses.append("status = ?")
                values.append(status)
            if prefix:
                clauses.append("COALESCE(key_prefix, 'LEGACY') LIKE ?")
                values.append(prefix + "%")
            if batch_id:
                clauses.append("batch_id = ?")
                values.append(batch_id.strip())
            if delivered is not None:
                clauses.append("delivered_at IS " + ("NOT NULL" if delivered else "NULL"))
            if team_id:
                clauses.append("team_id = ?")
                values.append(team_id)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = connection.execute(
                f"SELECT {_KEY_COLUMNS} FROM activation_keys{where} ORDER BY created_at DESC, key_id LIMIT ? OFFSET ?",
                (*values, limit, offset)).fetchall()
            counts = {row["status"]: int(row["count"]) for row in connection.execute(
                f"SELECT status, COUNT(*) AS count FROM activation_keys{where} GROUP BY status", values).fetchall()}
            total = int(connection.execute(
                f"SELECT COUNT(*) AS total FROM activation_keys{where}", values
            ).fetchone()["total"])
        return tuple(_summary(row) for row in rows), counts, total

    def take_batch(self, *, batch_id: str, count: int, recipient: str, actor: str,
                   team_id: str | None = None, now: int | None = None) -> tuple[KeySummary, ...]:
        """从批次里取走 N 枚未使用且未发放的码并记下发给谁；并发取用不会重复发同一枚。"""
        batch_id = batch_id.strip()
        recipient = recipient.strip()
        if not batch_id or not recipient:
            raise ValueError("batch_id and recipient are required")
        if not 1 <= count <= 200:
            raise ValueError("key take count is invalid")
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidates = connection.execute(
                """SELECT key_id FROM activation_keys
                   WHERE batch_id = ? AND status = 'unused' AND delivered_at IS NULL
                     AND (? IS NULL OR team_id = ?)
                   ORDER BY created_at, key_id LIMIT ?""",
                (batch_id, team_id, team_id, count),
            ).fetchall()
            if len(candidates) < count:
                raise ValueError("the batch does not hold that many undelivered unused keys")
            key_ids = [row["key_id"] for row in candidates]
            connection.executemany(
                "UPDATE activation_keys SET delivered_to = ?, delivered_at = ? WHERE key_id = ?",
                [(recipient, timestamp, key_id) for key_id in key_ids],
            )
            rows = connection.execute(
                f"SELECT {_KEY_COLUMNS} FROM activation_keys WHERE key_id IN ({','.join('?' * len(key_ids))})"
                " ORDER BY created_at, key_id",
                key_ids,
            ).fetchall()
        self._publish_change(
            kind="update", action="manage", team_id=team_id,
            data={"batch_id": batch_id, "count": len(key_ids), "recipient": recipient, "actor": actor},
        )
        return tuple(_summary(row) for row in rows)

    def release_key(self, key_id: str, *, actor: str, team_id: str | None = None,
                    now: int | None = None) -> None:
        """撤销发放记录：只在码还没被兑换时允许，回到"未发放"。"""
        key_id = key_id.strip()
        if not key_id:
            raise ValueError("key_id is required")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE activation_keys SET delivered_to = NULL, delivered_at = NULL
                   WHERE key_id = ? AND status = 'unused' AND delivered_at IS NOT NULL
                     AND (? IS NULL OR team_id = ?)""",
                (key_id, team_id, team_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("no delivered unused key was found")
        self._publish_change(
            kind="update", action="manage", team_id=team_id,
            data={"key_id": key_id, "recipient": None, "actor": actor},
        )

    def preview_update(
            self,
            key_id: str,
            *,
            note: str | None = None,
            permissions: frozenset[str] | None = None,
            expires_at: int | None = None,
            expires_at_provided: bool = False,
            status: str | None = None,
            team_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM activation_keys WHERE key_id = ? AND (? IS NULL OR team_id = ?)",
                (key_id, team_id, team_id),
            ).fetchone()
        if row is None:
            raise ValueError("activation key was not found")
        return self._build_update(row, note=note, permissions=permissions, expires_at=expires_at,
                                  expires_at_provided=expires_at_provided, status=status)

    @staticmethod
    def _build_update(row, *, note: str | None, permissions: frozenset[str] | None,
                      expires_at: int | None, expires_at_provided: bool, status: str | None) -> dict[str, Any]:
        current_permissions = frozenset(json.loads(row["permissions_json"]))
        next_permissions = current_permissions if permissions is None else permissions
        current_status = str(row["status"])
        next_status = current_status if status is None else status
        if next_status not in {"unused", "redeemed", "revoked"}:
            raise ValueError("key status is invalid")
        if current_status == "revoked" and next_status != "revoked":
            raise ValueError("revoked activation key cannot be restored")
        if current_status != "revoked" and next_status not in {current_status, "revoked"}:
            raise ValueError("activation key status transition is invalid")
        next_expiry = expires_at if expires_at_provided else row["expires_at"]
        removed = sorted(current_permissions - next_permissions)
        added = sorted(next_permissions - current_permissions)
        expiry_shortened = expires_at_provided and (
                row["expires_at"] is None and next_expiry is not None
                or row["expires_at"] is not None and next_expiry is not None and next_expiry < row["expires_at"]
        )
        revoked = current_status != "revoked" and next_status == "revoked"
        return {
            "key_id": row["key_id"],
            "current": {"note": row["note"], "permissions": sorted(current_permissions),
                        "expires_at": row["expires_at"], "status": current_status},
            "updated": {"note": row["note"] if note is None else note, "permissions": sorted(next_permissions),
                        "expires_at": next_expiry, "status": next_status},
            "diff": {"permissions_added": added, "permissions_removed": removed,
                     "expiry_shortened": expiry_shortened, "revoked": revoked},
            "requires_confirmation": bool(removed or expiry_shortened or revoked),
        }

    def update(
            self,
            key_id: str,
            *,
            actor: str,
            note: str | None = None,
            permissions: frozenset[str] | None = None,
            expires_at: int | None = None,
            expires_at_provided: bool = False,
            status: str | None = None,
            team_id: str | None = None,
            now: int | None = None,
    ) -> dict[str, Any]:
        if not key_id or not actor:
            raise ValueError("key update identity is invalid")
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM activation_keys WHERE key_id = ? AND (? IS NULL OR team_id = ?)",
                (key_id, team_id, team_id),
            ).fetchone()
            if row is None:
                raise ValueError("activation key was not found")
            result = self._build_update(row, note=note, permissions=permissions, expires_at=expires_at,
                                        expires_at_provided=expires_at_provided, status=status)
            updated = result["updated"]
            key_assignments = assignments_for_permissions(
                updated["permissions"], source_type="key", source_id=key_id
            )
            connection.execute(
                """UPDATE activation_keys
                   SET note=?,
                       permissions_json=?,
                       assignments_json=?,
                       expires_at=?,
                       status=?
                   WHERE key_id = ?""",
                (updated["note"], json.dumps(updated["permissions"]), serialize_assignments(key_assignments),
                 updated["expires_at"], updated["status"], key_id),
            )
            grant = connection.execute(
                "SELECT grant_id,status FROM grants WHERE source_type='key' AND source_id=? AND (? IS NULL OR team_id=?)",
                (key_id, team_id, team_id),
            ).fetchone()
            if row["status"] == "redeemed" and grant is None:
                raise ValueError("source grant was not found")
            if grant is not None:
                grant_status = "revoked" if updated["status"] == "revoked" else grant["status"]
                revoked_at = timestamp if grant_status == "revoked" else None
                self.authorization.sync_key_grant(
                    connection,
                    grant_id=grant["grant_id"],
                    assignments=key_assignments,
                    expires_at=updated["expires_at"],
                    status=grant_status,
                    revoked_at=revoked_at,
                    now=timestamp,
                )
                result["grant"] = {"grant_id": grant["grant_id"], "status": grant_status,
                                   "permissions": updated["permissions"], "expires_at": updated["expires_at"]}
                connection.execute(
                    "INSERT INTO audit_events (actor, action, target, metadata_json, created_at, team_id) VALUES (?, 'grant.sync_from_key', ?, ?, ?, ?)",
                    (f"github:{actor}", grant["grant_id"], json.dumps(redact_metadata({"key_id": key_id,
                                                                                       "diff": result["diff"]}),
                                                                      ensure_ascii=False, sort_keys=True), timestamp,
                     team_id),
                )
            connection.execute(
                "INSERT INTO audit_events (actor, action, target, metadata_json, created_at, team_id) VALUES (?, 'key.update', ?, ?, ?, ?)",
                (f"github:{actor}", key_id, json.dumps(redact_metadata({"diff": result["diff"],
                                                                        "updated": updated}), ensure_ascii=False,
                                                       sort_keys=True), timestamp, team_id),
            )
        self._publish_change(
            kind="update",
            action="manage",
            team_id=team_id,
            data={"key_id": key_id, "diff": result["diff"]},
        )
        return result

    def _publish_change(self, *, kind: str, action: str, team_id: str | None, data: dict[str, object]) -> None:
        if self.events is None:
            return
        self.events.publish(ResourceChanged(
            kind=kind,
            node=f"team.{team_id or 'default'}.keys",
            action=action,
            data=data,
            team_id=team_id,
        ))
