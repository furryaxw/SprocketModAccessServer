from __future__ import annotations

import json
import secrets
import time
from collections.abc import Iterable, Mapping

from ...domain.models import GrantRecord, GrantStatus, PermissionAssignment
from ...domain.permissions import evaluate, node_to_permission, permission_to_node
from ...infrastructure.database import SQLiteDatabase
from ...infrastructure.events import ResourceChanged

# 分配列表的行集合：直接授权的逐节点行 ∪ 模板套用展开的逐节点行。
# 模板套用的节点存在 `template_assignments`，只查 `grant_assignments` 会让这类授权在
# 列表里完全消失（概览按 grants 计数，列表按逐节点行展示）。
# 模板行的 `assignment_id` 用 grant_id：生命周期操作按 grant 生效，节点内容仍由模板决定。
ASSIGNMENT_SCOPE = """
    SELECT ga.assignment_id AS assignment_id,
           ga.grant_id AS grant_id,
           g.github_user_id AS github_user_id,
           g.team_id AS team_id,
           ga.node AS node,
           ga.effect AS effect,
           ga.priority AS priority,
           ga.grant_effect AS grant_effect,
           ga.source_type AS source_type,
           ga.source_id AS source_id,
           g.status AS status,
           g.created_at AS created_at,
           COALESCE(ga.expires_at, g.expires_at) AS expires_at,
           COALESCE(ga.revoked_at, g.revoked_at) AS revoked_at,
           'assignment' AS origin,
           NULL AS template_id
    FROM grant_assignments ga
    JOIN grants g ON g.grant_id = ga.grant_id
    UNION ALL
    SELECT g.grant_id AS assignment_id,
           g.grant_id AS grant_id,
           g.github_user_id AS github_user_id,
           g.team_id AS team_id,
           ta.node AS node,
           ta.effect AS effect,
           ta.priority AS priority,
           ta.grant_effect AS grant_effect,
           'permission_template' AS source_type,
           g.source_id AS source_id,
           g.status AS status,
           g.created_at AS created_at,
           COALESCE(ta.expires_at, g.expires_at) AS expires_at,
           COALESCE(ta.revoked_at, g.revoked_at) AS revoked_at,
           'template' AS origin,
           g.template_id AS template_id
    FROM template_assignments ta
    JOIN grants g ON g.template_id = ta.template_id
"""


def assignments_for_permissions(
        permissions: Iterable[str] | Mapping[str, str],
        *,
        source_type: str,
        source_id: str,
        priority: int = 0,
) -> tuple[PermissionAssignment, ...]:
    """构建 Assignment 集合。

    `permissions` 可以是 `{节点: effect}` 映射，也可以是纯节点序列（一律 `allow`）；
    `effect` 只允许 `allow` / `deny`。
    """
    if isinstance(permissions, Mapping):
        effects = {
            permission_to_node(node): str(effect).strip().casefold()
            for node, effect in permissions.items()
        }
    else:
        effects = {permission_to_node(value): "allow" for value in permissions}
    if not effects:
        raise ValueError("at least one permission is required")
    invalid = sorted(effect for effect in effects.values() if effect not in {"allow", "deny"})
    if invalid:
        raise ValueError("permission effect must be allow or deny")
    return tuple(
        PermissionAssignment(
            assignment_id=f"{source_type}:{source_id}:{index}",
            node=node,
            effect=effects[node],
            priority=priority,
            source_type=source_type,
            source_id=source_id,
        )
        for index, node in enumerate(sorted(effects))
    )


def serialize_assignments(assignments: Iterable[PermissionAssignment]) -> str:
    return json.dumps([
        {
            "assignment_id": item.assignment_id,
            "node": item.node,
            "effect": item.effect,
            "priority": item.priority,
            "grant": item.grant,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "starts_at": item.starts_at,
            "expires_at": item.expires_at,
            "revoked_at": item.revoked_at,
        }
        for item in assignments
    ], ensure_ascii=False, sort_keys=True)


def deserialize_assignments(
        value: str | None,
        *,
        source_type: str,
        source_id: str,
) -> tuple[PermissionAssignment, ...]:
    try:
        raw_items = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("assignment data is invalid") from exc
    if not isinstance(raw_items, list):
        raise ValueError("assignment data is invalid")
    result: list[PermissionAssignment] = []
    for index, raw in enumerate(raw_items):
        if isinstance(raw, str):
            raw = {"node": raw}
        if not isinstance(raw, dict):
            raise ValueError("assignment data is invalid")
        node = raw.get("node", raw.get("permission"))
        if not isinstance(node, str):
            raise ValueError("assignment node is required")
        result.append(PermissionAssignment(
            assignment_id=str(raw.get("assignment_id") or f"{source_type}:{source_id}:{index}"),
            node=permission_to_node(node),
            effect=str(raw.get("effect", "allow")),
            priority=int(raw.get("priority", 0)),
            grant=str(raw.get("grant", raw.get("grant_effect", "allow"))),
            source_type=str(raw.get("source_type") or source_type),
            source_id=str(raw.get("source_id") or source_id),
            starts_at=raw.get("starts_at"),
            expires_at=raw.get("expires_at"),
            revoked_at=raw.get("revoked_at"),
        ))
    if not result:
        raise ValueError("at least one permission is required")
    return tuple(result)


def public_permissions(assignments: Iterable[PermissionAssignment]) -> frozenset[str]:
    return frozenset(
        node_to_permission(item.node)
        for item in assignments
        if item.effect == "allow"
    )


class SQLiteAuthorizationStore:
    """SQLite adapter for assignment-based Key and Grant authorization."""

    def __init__(self, database: SQLiteDatabase, *, events=None):
        self.database = database
        self.events = events
        self.database.initialize()

    @staticmethod
    def _assignments_for_grant(connection, grant_id: str, template_id: str | None) -> tuple[PermissionAssignment, ...]:
        rows = connection.execute(
            "SELECT * FROM grant_assignments WHERE grant_id=? ORDER BY priority DESC, assignment_id",
            (grant_id,),
        ).fetchall()
        if rows:
            return tuple(PermissionAssignment(
                assignment_id=row["assignment_id"],
                node=row["node"],
                effect=row["effect"],
                priority=row["priority"],
                grant=row["grant_effect"],
                source_type=row["source_type"],
                source_id=row["source_id"],
                starts_at=row["starts_at"],
                expires_at=row["expires_at"],
                revoked_at=row["revoked_at"],
            ) for row in rows)
        if not template_id:
            return ()
        rows = connection.execute(
            "SELECT * FROM template_assignments WHERE template_id=? ORDER BY priority DESC, assignment_id",
            (template_id,),
        ).fetchall()
        return tuple(PermissionAssignment(
            assignment_id=row["assignment_id"],
            node=row["node"],
            effect=row["effect"],
            priority=row["priority"],
            grant=row["grant_effect"],
            source_type="template",
            source_id=template_id,
            starts_at=row["starts_at"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
        ) for row in rows)

    @classmethod
    def _grant_record(cls, connection, row) -> GrantRecord:
        return GrantRecord(
            grant_id=row["grant_id"],
            github_user_id=row["github_user_id"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            team_id=row["team_id"],
            status=GrantStatus(row["status"]),
            starts_at=row["starts_at"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            template_id=row["template_id"],
            assignments=cls._assignments_for_grant(connection, row["grant_id"], row["template_id"]),
        )

    def issue_key(
            self,
            *,
            key_id: str,
            key_hash: str,
            permissions: frozenset[str],
            expires_at: int | None,
            team_id: str | None = None,
            assignments: tuple[PermissionAssignment, ...] | None = None,
            template_id: str | None = None,
            key_kind: str = "snapshot",
            now: int | None = None,
    ) -> None:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if not key_id or not key_hash:
            raise ValueError("key identity is required")
        if key_kind not in {"snapshot", "template"}:
            raise ValueError("key kind is invalid")
        effective_assignments = assignments or assignments_for_permissions(
            permissions, source_type="key", source_id=key_id
        )
        public = public_permissions(effective_assignments)
        if not public:
            raise ValueError("at least one permission is required")
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO activation_keys
                   (key_id, key_hash, permissions_json, assignments_json, template_id, key_kind,
                    status, expires_at, created_at, team_id)
                   VALUES (?, ?, ?, ?, ?, ?, 'unused', ?, ?, ?)""",
                (
                    key_id,
                    key_hash,
                    json.dumps(sorted(public)),
                    serialize_assignments(effective_assignments),
                    template_id,
                    key_kind,
                    expires_at,
                    timestamp,
                    team_id,
                ),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO permission_nodes(node) VALUES (?)",
                [(item.node,) for item in effective_assignments],
            )
        self._publish_change(
            kind="create",
            action="manage",
            node="keys",
            team_id=team_id,
            data={"key_id": key_id, "key_kind": key_kind},
        )

    def redeem_key(
            self,
            *,
            key_hash: str,
            github_user_id: str,
            now: int | None = None,
            team_id: str | None = None,
    ) -> GrantRecord:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if not key_hash or not github_user_id:
            raise ValueError("key hash and GitHub user ID are required")
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM activation_keys WHERE key_hash=? AND (? IS NULL OR team_id=?)",
                (key_hash, team_id, team_id),
            ).fetchone()
            if row is None or row["status"] != "unused":
                raise ValueError("activation key is unavailable")
            if row["expires_at"] is not None and row["expires_at"] <= timestamp:
                raise ValueError("activation key has expired")
            connection.execute(
                """INSERT INTO users(github_user_id, created_at, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(github_user_id)
                       DO UPDATE SET updated_at=excluded.updated_at""",
                (github_user_id, timestamp, timestamp),
            )
            assignments = (
                deserialize_assignments(row["assignments_json"], source_type="key", source_id=row["key_id"])
                if row["assignments_json"] else
                assignments_for_permissions(json.loads(row["permissions_json"]), source_type="key",
                                            source_id=row["key_id"])
            )
            grant_id = secrets.token_hex(16)
            connection.execute(
                """UPDATE activation_keys
                   SET status='redeemed',
                       redeemed_github_user_id=?,
                       redeemed_at=?
                   WHERE key_id = ?
                     AND status = 'unused'""",
                (github_user_id, timestamp, row["key_id"]),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ValueError("activation key is unavailable")
            connection.execute(
                """INSERT INTO grants
                   (grant_id, github_user_id, source_type, source_id, template_id, team_id,
                    status, expires_at, created_by, created_at, updated_at)
                   VALUES (?, ?, 'key', ?, ?, ?, 'active', ?, ?, ?, ?)""",
                (
                    grant_id,
                    github_user_id,
                    row["key_id"],
                    row["template_id"],
                    row["team_id"],
                    row["expires_at"],
                    github_user_id,
                    timestamp,
                    timestamp,
                ),
            )
            connection.executemany(
                """INSERT INTO grant_assignments
                   (assignment_id, grant_id, node, effect, priority, grant_effect,
                    source_type, source_id, starts_at, expires_at, revoked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        f"grant:{grant_id}:{index}",
                        grant_id,
                        item.node,
                        item.effect,
                        item.priority,
                        item.grant,
                        item.source_type,
                        item.source_id,
                        item.starts_at,
                        item.expires_at,
                        item.revoked_at,
                    )
                    for index, item in enumerate(assignments)
                ],
            )
            connection.executemany(
                "INSERT OR IGNORE INTO permission_nodes(node) VALUES (?)",
                [(item.node,) for item in assignments],
            )
            record = self._grant_record(
                connection,
                connection.execute("SELECT * FROM grants WHERE grant_id=?", (grant_id,)).fetchone(),
            )
        self._publish_change(
            kind="create",
            action="manage",
            node="permission_assignments",
            team_id=team_id,
            data={"grant_id": grant_id, "github_user_id": github_user_id},
        )
        return record

    @staticmethod
    def sync_key_grant(
            connection,
            *,
            grant_id: str,
            assignments: tuple[PermissionAssignment, ...],
            expires_at: int | None,
            status: str,
            revoked_at: int | None,
            now: int,
    ) -> None:
        connection.execute(
            """UPDATE grants
               SET expires_at=?,
                   status=?,
                   revoked_at=?,
                   updated_at=?
               WHERE grant_id=?""",
            (expires_at, status, revoked_at, now, grant_id),
        )
        connection.execute("DELETE FROM grant_assignments WHERE grant_id=?", (grant_id,))
        connection.executemany(
            """INSERT INTO grant_assignments
               (assignment_id, grant_id, node, effect, priority, grant_effect, source_type, source_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    f"grant:{grant_id}:{index}",
                    grant_id,
                    item.node,
                    item.effect,
                    item.priority,
                    item.grant,
                    item.source_type,
                    item.source_id,
                )
                for index, item in enumerate(assignments)
            ],
        )

    def grants(self, github_user_id: str, *, team_id: str | None = None) -> tuple[GrantRecord, ...]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """SELECT *
                   FROM grants
                   WHERE github_user_id = ?
                     AND (? IS NULL OR team_id = ?)
                   ORDER BY created_at, grant_id""",
                (github_user_id, team_id, team_id),
            ).fetchall()
            return tuple(self._grant_record(connection, row) for row in rows)

    def get_grant(self, grant_id: str, *, team_id: str | None = None) -> GrantRecord | None:
        with self.database.transaction() as connection:
            row = self._get_grant(connection, grant_id, team_id)
            return None if row is None else self._grant_record(connection, row)

    def grant_id_for_assignment(self, assignment_id: str, *, team_id: str | None = None) -> str | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT g.grant_id
                   FROM grants g
                            JOIN grant_assignments ga ON ga.grant_id = g.grant_id
                   WHERE ga.assignment_id = ?
                     AND (? IS NULL OR g.team_id = ?)""",
                (assignment_id, team_id, team_id),
            ).fetchone()
        return None if row is None else str(row["grant_id"])

    def create_grant(
            self,
            *,
            github_user_id: str,
            team_id: str,
            permissions: Mapping[str, str],
            expires_at: int | None,
            created_by: str,
            now: int | None = None,
    ) -> GrantRecord:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if not github_user_id or not team_id or not created_by:
            raise ValueError("grant identity is required")
        if not permissions:
            raise ValueError("at least one permission is required")
        if expires_at is not None and expires_at <= timestamp:
            raise ValueError("grant expiry must be in the future")
        grant_id = secrets.token_hex(16)
        assignments = assignments_for_permissions(
            permissions,
            source_type="direct",
            source_id=grant_id,
        )
        with self.database.transaction() as connection:
            user = connection.execute(
                "SELECT 1 FROM users WHERE github_user_id=? AND status='active'",
                (github_user_id,),
            ).fetchone()
            if user is None:
                raise ValueError("target user was not found")
            team = connection.execute(
                "SELECT 1 FROM teams WHERE team_id=? AND status='active'",
                (team_id,),
            ).fetchone()
            if team is None:
                raise ValueError("Team was not found")
            connection.execute(
                """INSERT INTO grants
                   (grant_id, github_user_id, source_type, source_id, team_id, status,
                    expires_at, created_by, created_at, updated_at)
                   VALUES (?, ?, 'direct', ?, ?, 'active', ?, ?, ?, ?)""",
                (grant_id, github_user_id, grant_id, team_id, expires_at,
                 created_by, timestamp, timestamp),
            )
            connection.executemany(
                """INSERT INTO grant_assignments
                   (assignment_id, grant_id, node, effect, priority, grant_effect,
                    source_type, source_id)
                   VALUES (?, ?, ?, ?, 0, 'allow', 'direct', ?)""",
                [
                    (item.assignment_id, grant_id, item.node, item.effect, grant_id)
                    for item in assignments
                ],
            )
            connection.executemany(
                "INSERT OR IGNORE INTO permission_nodes(node) VALUES (?)",
                [(item.node,) for item in assignments],
            )
            record = self._grant_record(
                connection,
                connection.execute("SELECT * FROM grants WHERE grant_id=?", (grant_id,)).fetchone(),
            )
        self._publish_change(
            kind="create",
            action="manage",
            node="permission_assignments",
            team_id=team_id,
            data={"grant_id": grant_id, "github_user_id": github_user_id},
        )
        return record

    def _active_assignments(self, github_user_id: str, *, team_id: str | None, now: int) -> tuple[
        PermissionAssignment, ...]:
        return tuple(
            assignment
            for grant in self.grants(github_user_id, team_id=team_id)
            if grant.is_active(now=now)
            for assignment in grant.assignments
        )

    def allows(
            self,
            github_user_id: str,
            permission: str,
            *,
            now: int | None = None,
            team_id: str | None = None,
    ) -> bool:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        assignments = self._active_assignments(github_user_id, team_id=team_id, now=timestamp)
        return evaluate(assignments, permission_to_node(permission), now=timestamp).allowed

    def permissions(self, github_user_id: str, *, now: int | None = None, team_id: str | None = None) -> frozenset[str]:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        assignments = self._active_assignments(github_user_id, team_id=team_id, now=timestamp)
        nodes = {item.node for item in assignments}
        return frozenset(
            node_to_permission(node)
            for node in nodes
            if evaluate(assignments, node, now=timestamp).allowed
        )

    def search_grants(
            self,
            *,
            github_user_id: str | None = None,
            status: str | None = None,
            limit: int = 50,
            offset: int = 0,
            team_id: str | None = None,
    ) -> tuple[GrantRecord, ...]:
        if status is not None and status not in {"active", "revoked", "suspended"}:
            raise ValueError("grant status is invalid")
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("grant pagination is invalid")
        clauses: list[str] = []
        values: list[object] = []
        if github_user_id:
            clauses.append("github_user_id=?")
            values.append(github_user_id)
        if status:
            clauses.append("status=?")
            values.append(status)
        if team_id:
            clauses.append("team_id=?")
            values.append(team_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM grants{where} ORDER BY created_at DESC,grant_id LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            return tuple(self._grant_record(connection, row) for row in rows)

    def search_assignments(
            self,
            *,
            user_id: str | None = None,
            user_id_like: str | None = None,
            status: str | None = None,
            expiry: str | None = None,
            node: str | None = None,
            node_like: str | None = None,
            source: str | None = None,
            limit: int = 50,
            offset: int = 0,
            team_id: str | None = None,
            now: int | None = None,
    ) -> tuple[list[dict[str, object]], int]:
        if status is not None and status not in {"active", "revoked", "suspended", "expired"}:
            raise ValueError("grant status is invalid")
        if expiry is not None and expiry not in {"expired", "permanent"}:
            raise ValueError("grant expiry filter is invalid")
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("assignment pagination is invalid")
        timestamp = int(time.time()) if now is None or now <= 0 else now
        clauses: list[str] = []
        values: list[object] = []
        if user_id:
            # 等值匹配：按 id 精确查找的人不能被前缀相同的 id 连带命中。
            clauses.append("github_user_id=?")
            values.append(user_id)
        if user_id_like:
            clauses.append("github_user_id LIKE ?")
            values.append(f"%{user_id_like}%")
        if status:
            if status == "expired":
                clauses.append(
                    "status='active' AND expires_at IS NOT NULL AND expires_at<=?"
                )
                values.append(timestamp)
            else:
                clauses.append("status=?")
                values.append(status)
        if team_id:
            clauses.append("team_id=?")
            values.append(team_id)
        if node:
            clauses.append("node=?")
            values.append(node)
        if node_like:
            clauses.append("node LIKE ?")
            values.append(f"%{node_like}%")
        if source:
            clauses.append(
                "(LOWER(COALESCE(source_type, '')) || ':' || "
                "LOWER(COALESCE(source_id, ''))) LIKE ?"
            )
            values.append(f"%{source.casefold()}%")
        if expiry == "expired":
            clauses.append("expires_at IS NOT NULL AND expires_at<=?")
            values.append(timestamp)
        elif expiry == "permanent":
            clauses.append("expires_at IS NULL")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.database.transaction() as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) AS total FROM ({ASSIGNMENT_SCOPE}) rows{where}",
                values,
            ).fetchone()["total"])
            rows = connection.execute(
                f"""SELECT * FROM ({ASSIGNMENT_SCOPE}) rows{where}
                    ORDER BY created_at DESC, assignment_id
                    LIMIT ? OFFSET ?""",
                (*values, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows], total

    def _get_grant(self, connection, grant_id: str, team_id: str | None):
        return connection.execute(
            "SELECT * FROM grants WHERE grant_id=? AND (? IS NULL OR team_id=?)",
            (grant_id, team_id, team_id),
        ).fetchone()

    def get_grant(self, grant_id: str, *, team_id: str | None = None) -> GrantRecord | None:
        with self.database.transaction() as connection:
            row = self._get_grant(connection, grant_id, team_id)
            return None if row is None else self._grant_record(connection, row)

    def revoke_grant(self, grant_id: str, *, now: int | None = None, team_id: str | None = None) -> None:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE grants
                   SET status='revoked',
                       revoked_at=?,
                       updated_at=?
                   WHERE grant_id = ?
                     AND status = 'active'
                     AND (? IS NULL OR team_id = ?)""",
                (timestamp, timestamp, grant_id, team_id, team_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("active grant was not found")
            row = connection.execute(
                "SELECT github_user_id FROM grants WHERE grant_id = ?", (grant_id,),
            ).fetchone()
        self._publish_change(
            kind="update",
            action="manage",
            node="permission_assignments",
            team_id=team_id,
            data={"grant_id": grant_id, "status": "revoked",
                  "github_user_id": row["github_user_id"] if row else None},
        )

    def set_grant_status(self, grant_id: str, status: str, *, now: int | None = None,
                         team_id: str | None = None) -> None:
        if status not in {"active", "revoked", "suspended"}:
            raise ValueError("grant status is invalid")
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            row = self._get_grant(connection, grant_id, team_id)
            if row is None or row["status"] == "revoked":
                raise ValueError("active grant was not found")
            connection.execute(
                "UPDATE grants SET status=?,revoked_at=?,updated_at=? WHERE grant_id=?",
                (status, timestamp if status == "revoked" else None, timestamp, grant_id),
            )
        self._publish_change(
            kind="update",
            action="manage",
            node="permission_assignments",
            team_id=team_id,
            data={"grant_id": grant_id, "status": status, "github_user_id": row["github_user_id"]},
        )

    def extend_grant(self, grant_id: str, expires_at: int, *, now: int | None = None,
                     team_id: str | None = None) -> None:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if expires_at <= timestamp:
            raise ValueError("grant expiry must be in the future")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT expires_at, github_user_id FROM grants WHERE grant_id=? AND status='active' AND (? IS NULL OR team_id=?)",
                (grant_id, team_id, team_id),
            ).fetchone()
            if row is None:
                raise ValueError("active grant was not found")
            if row["expires_at"] is not None and expires_at <= row["expires_at"]:
                raise ValueError("grant expiry can only be extended")
            connection.execute("UPDATE grants SET expires_at=?,updated_at=? WHERE grant_id=?",
                               (expires_at, timestamp, grant_id))
        self._publish_change(
            kind="update",
            action="manage",
            node="permission_assignments",
            team_id=team_id,
            data={"grant_id": grant_id, "expires_at": expires_at, "github_user_id": row["github_user_id"]},
        )

    def preview_grant_update(
            self,
            grant_id: str,
            *,
            permissions: Mapping[str, str] | None = None,
            expires_at: int | None = None,
            expires_at_provided: bool = False,
            team_id: str | None = None,
    ) -> dict[str, object]:
        with self.database.transaction() as connection:
            row = self._get_grant(connection, grant_id, team_id)
            if row is None:
                raise ValueError("grant was not found")
            current = self._grant_record(connection, row)
        current_permissions = current.permissions
        current_effects = {item.node: item.effect for item in current.assignments}
        updated = current_permissions if permissions is None else frozenset(permissions)
        updated_effects = dict(current_effects) if permissions is None else {
            permission_to_node(node): str(effect).strip().casefold()
            for node, effect in permissions.items()
        }
        next_expiry = expires_at if expires_at_provided else current.expires_at
        permissions_added = sorted(updated - current_permissions)
        permissions_removed = sorted(current_permissions - updated)
        # 两边都有的节点，effect 变了才算"效果变化"。
        effect_changed = any(
            updated_effects.get(node) != current_effects.get(node)
            for node in sorted(updated & current_permissions)
        )
        expiry_shortened = expires_at_provided and (
                current.expires_at is None and next_expiry is not None
                or current.expires_at is not None and next_expiry is not None
                and next_expiry < current.expires_at
        )
        denied_added = sorted(
            node for node in permissions_added if updated_effects.get(node) == "deny"
        )
        return {
            "grant_id": grant_id,
            "current": {"permissions": sorted(current_permissions), "effects": current_effects,
                        "expires_at": current.expires_at, "status": current.status.value,
                        "github_user_id": current.github_user_id},
            "updated": {"permissions": sorted(updated), "effects": updated_effects,
                        "expires_at": next_expiry, "status": current.status.value,
                        "github_user_id": current.github_user_id},
            "diff": {
                "permissions_added": permissions_added,
                "permissions_removed": permissions_removed,
                "effect_changed": effect_changed,
                "priority_changed": False,
                "expiry_shortened": expiry_shortened,
                "expiry_changed": expires_at_provided and next_expiry != current.expires_at,
                "affected_users": [current.github_user_id],
                "access_impact": {
                    "adds_access": bool(permissions_added) and not denied_added,
                    "removes_access": bool(permissions_removed or expiry_shortened or denied_added
                                           or effect_changed),
                    "changes_expiry": expires_at_provided and next_expiry != current.expires_at,
                },
            },
        }

    def update_grant(
            self,
            grant_id: str,
            *,
            permissions: Mapping[str, str] | None = None,
            expires_at: int | None = None,
            expires_at_provided: bool = False,
            now: int | None = None,
            team_id: str | None = None,
    ) -> GrantRecord:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._get_grant(connection, grant_id, team_id)
            if row is None:
                raise ValueError("grant was not found")
            if row["status"] == "revoked":
                raise ValueError("revoked grant cannot be restored")
            current = self._grant_record(connection, row)
            current_permissions = current.permissions
            updated_permissions = current_permissions if permissions is None else frozenset(permissions)
            if not updated_permissions:
                raise ValueError("at least one permission is required")
            updated_expiry = expires_at if expires_at_provided else row["expires_at"]
            if updated_expiry is not None and updated_expiry <= timestamp:
                raise ValueError("grant expiry must be in the future")
            if permissions is not None:
                # 提交的是 {节点: effect}：整表替换，effect 直接落库。
                assignments = assignments_for_permissions(
                    permissions, source_type="grant", source_id=grant_id
                )
                connection.execute("DELETE FROM grant_assignments WHERE grant_id=?", (grant_id,))
                connection.executemany(
                    """INSERT INTO grant_assignments
                       (assignment_id, grant_id, node, effect, priority, grant_effect, source_type, source_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (item.assignment_id, grant_id, item.node, item.effect, item.priority,
                         item.grant, item.source_type, item.source_id)
                        for item in assignments
                    ],
                )
            connection.execute("UPDATE grants SET expires_at=?,updated_at=? WHERE grant_id=?",
                               (updated_expiry, timestamp, grant_id))
            updated = connection.execute("SELECT * FROM grants WHERE grant_id=?", (grant_id,)).fetchone()
            record = self._grant_record(connection, updated)
        self._publish_change(
            kind="update",
            action="manage",
            node="permission_assignments",
            team_id=team_id,
            data={"grant_id": grant_id, "permissions": sorted(updated_permissions),
                  "github_user_id": row["github_user_id"]},
        )
        return record

    def _publish_change(self, *, kind: str, action: str, node: str, team_id: str | None,
                        data: dict[str, object]) -> None:
        if self.events is None:
            return
        self.events.publish(ResourceChanged(
            kind=kind,
            node=f"team.{team_id or 'default'}.{node}",
            action=action,
            data=data,
            team_id=team_id,
        ))
