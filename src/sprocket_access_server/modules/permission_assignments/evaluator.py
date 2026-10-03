from __future__ import annotations

import time
from dataclasses import dataclass

from ...domain.errors import ApiError
from ...domain.models import PermissionAssignment
from ...domain.permissions import evaluate, node_to_permission, permission_to_node, team_node
from ...domain.resources import SYSTEM_TEAM_ID, package_resource_node
from ...infrastructure.database import SQLiteDatabase
from ..permission_templates.instance_nodes import is_template_node, template_id_for_node


@dataclass(frozen=True)
class AuthorizationContext:
    user_id: str
    system_permissions: frozenset[str]
    team_id: str | None = None
    team_permissions: frozenset[str] = frozenset()
    content_permissions: frozenset[str] = frozenset()
    tester_scopes: frozenset[str] = frozenset()
    effective_permissions: frozenset[str] = frozenset()
    grantable_permissions: frozenset[str] = frozenset()
    permission_assignments: tuple[PermissionAssignment, ...] = ()


class AuthorizationService:
    """Single decision boundary backed only by Permission Assignment records."""

    def __init__(self, database: SQLiteDatabase):
        self.database = database
        self.database.initialize()

    @staticmethod
    def _active_assignments(connection, user_id: str, team_id: str | None,
                            timestamp: int) -> tuple[PermissionAssignment, ...]:
        values: list[object] = [user_id]
        team_filter = ""
        if team_id is not None:
            team_filter = " AND (team_id IS NULL OR team_id=?)"
            values.append(team_id)
        grants = connection.execute(
            f"SELECT * FROM grants WHERE github_user_id=?{team_filter}",
            tuple(values),
        ).fetchall()
        assignments: list[PermissionAssignment] = []
        for grant in grants:
            if grant["status"] != "active":
                continue
            if grant["starts_at"] is not None and grant["starts_at"] > timestamp:
                continue
            if grant["expires_at"] is not None and grant["expires_at"] <= timestamp:
                continue
            if grant["template_id"]:
                template = connection.execute(
                    "SELECT status FROM permission_templates WHERE template_id=?",
                    (grant["template_id"],),
                ).fetchone()
                if template is None or template["status"] != "active":
                    continue
                rows = connection.execute(
                    "SELECT * FROM template_assignments WHERE template_id=? ORDER BY priority DESC,assignment_id",
                    (grant["template_id"],),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM grant_assignments WHERE grant_id=? ORDER BY priority DESC,assignment_id",
                    (grant["grant_id"],),
                ).fetchall()
            assignments.extend(PermissionAssignment(
                assignment_id=row["assignment_id"],
                node=row["node"],
                effect=row["effect"],
                priority=row["priority"],
                grant=row["grant_effect"],
                source_type="grant",
                source_id=grant["grant_id"],
                starts_at=row["starts_at"],
                expires_at=row["expires_at"],
                revoked_at=row["revoked_at"],
            ) for row in rows)
        return tuple(AuthorizationService._expand_template_nodes(connection, assignments, timestamp))

    @staticmethod
    def _expand_template_nodes(connection, assignments: list[PermissionAssignment],
                               timestamp: int) -> list[PermissionAssignment]:
        """把模板实例节点展开成模板成员节点。

        模板实例在后端树里就是节点（`team.<team>.permission_templates.<segment>`），授权里
        出现该节点时按模板当前内容展开，所以模板内容变化会继续生效。展开按层进行，
        用已访问集合防住模板互引造成的环。
        """
        if not any(is_template_node(item.node) for item in assignments):
            return assignments
        expanded: list[PermissionAssignment] = []
        pending = list(assignments)
        seen_templates: set[str] = set()
        seen_nodes: set[str] = set()
        while pending:
            item = pending.pop(0)
            if is_template_node(item.node):
                template_id = template_id_for_node(connection, item.node)
                if template_id is None or template_id in seen_templates:
                    continue
                seen_templates.add(template_id)
                template = connection.execute(
                    "SELECT status FROM permission_templates WHERE template_id=?",
                    (template_id,),
                ).fetchone()
                if template is None or template["status"] != "active":
                    continue
                rows = connection.execute(
                    "SELECT * FROM template_assignments WHERE template_id=? ORDER BY priority DESC,assignment_id",
                    (template_id,),
                ).fetchall()
                for row in rows:
                    pending.append(PermissionAssignment(
                        assignment_id=f"{item.assignment_id}:{row['assignment_id']}",
                        node=row["node"],
                        effect=row["effect"],
                        priority=row["priority"],
                        grant=row["grant_effect"],
                        source_type="template",
                        source_id=template_id,
                        starts_at=row["starts_at"],
                        expires_at=row["expires_at"],
                        revoked_at=row["revoked_at"],
                    ))
                continue
            key = f"{item.node}|{item.effect}|{item.priority}"
            if key in seen_nodes:
                continue
            seen_nodes.add(key)
            expanded.append(item)
        return expanded

    @classmethod
    def _effective_permissions(cls, connection, user_id: str, team_id: str | None,
                               timestamp: int) -> frozenset[str]:
        assignments = cls._active_assignments(connection, user_id, team_id, timestamp)
        candidates = {item.node for item in assignments if item.effect == "allow"}
        return frozenset(node_to_permission(node) for node in candidates
                         if evaluate(assignments, node, now=timestamp).allowed)

    def context(self, user_id: str, *, team_id: str | None = None, now: int | None = None) -> AuthorizationContext:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            user = connection.execute(
                "SELECT status FROM users WHERE github_user_id=?", (user_id,)
            ).fetchone()
            if user is None or user["status"] != "active":
                raise ApiError(403, "system_denied", "user is not active")
            assignments = list(self._active_assignments(connection, user_id, team_id, timestamp))
            if team_id is None:
                effective = frozenset(node_to_permission(item.node) for item in assignments
                                      if evaluate(assignments, item.node, now=timestamp).allowed)
                grantable = frozenset(node_to_permission(item.node) for item in assignments
                                      if evaluate(assignments, item.node, now=timestamp).grantable)
                return AuthorizationContext(user_id, frozenset(),
                                            effective_permissions=effective,
                                            grantable_permissions=grantable,
                                            permission_assignments=tuple(assignments))
            team = connection.execute(
                "SELECT status FROM teams WHERE team_id=?", (team_id,)
            ).fetchone()
            if team is None:
                raise ApiError(404, "team_not_found", "Team does not exist")
            if team["status"] != "active":
                raise ApiError(403, "team_access_denied", "Team is unavailable")
        effective = frozenset(node_to_permission(item.node) for item in assignments
                              if evaluate(assignments, item.node, now=timestamp).allowed)
        grantable = frozenset(node_to_permission(item.node) for item in assignments
                              if evaluate(assignments, item.node, now=timestamp).grantable)
        return AuthorizationContext(user_id, frozenset(), team_id,
                                    frozenset(), effective, frozenset(),
                                    effective, grantable, tuple(assignments))

    def require_system(self, user_id: str, permission: str, *, now: int | None = None) -> AuthorizationContext:
        context = self.context(user_id, now=now)
        requested = permission_to_node(permission)
        if not evaluate(context.permission_assignments, requested,
                        now=int(time.time()) if now is None or now <= 0 else now).allowed:
            raise ApiError(403, "system_denied", "system permission is required")
        return context

    def require_team(self, user_id: str, team_id: str | None, capability: str, *,
                     now: int | None = None) -> AuthorizationContext:
        if not team_id:
            raise ApiError(400, "team_context_required", "explicit Team context is required")
        context = self.context(user_id, team_id=team_id, now=now)
        requested = team_node(team_id, capability.removeprefix("team."))
        if not evaluate(context.permission_assignments, requested,
                        now=int(time.time()) if now is None or now <= 0 else now).allowed:
            raise ApiError(403, "team_permission_denied", "Team permission is required")
        return context

    def require_system_or_team(
            self,
            user_id: str,
            system_permission: str,
            team_id: str,
            team_permission: str,
            *,
            now: int | None = None,
    ) -> AuthorizationContext:
        try:
            return self.require_system(user_id, system_permission, now=now)
        except ApiError as exc:
            if exc.code != "system_denied":
                raise
        return self.require_team(user_id, team_id, team_permission, now=now)

    def allows(self, user_id: str, permission: str, *, now: int | None = None,
               team_id: str | None = None) -> bool:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        context = self.context(user_id, team_id=team_id, now=timestamp)
        return evaluate(context.permission_assignments, permission_to_node(permission), now=timestamp).allowed

    def require_grantable(self, user_id: str, permissions: list[str] | tuple[str, ...], *,
                          team_id: str | None = None, now: int | None = None) -> AuthorizationContext:
        """Require that an actor can delegate every requested permission node."""
        timestamp = int(time.time()) if now is None or now <= 0 else now
        context = self.context(user_id, team_id=team_id, now=timestamp)
        for permission in permissions:
            requested = permission_to_node(permission)
            decision = evaluate(context.permission_assignments, requested, now=timestamp)
            if not decision.allowed or not decision.grantable:
                raise ApiError(403, "grant_scope_denied",
                               f"permission cannot be granted: {permission}")
        return context

    def memberships(self, user_id: str) -> list[dict[str, object]]:
        timestamp = int(time.time())
        with self.database.transaction() as connection:
            teams = connection.execute(
                "SELECT team_id,name,status,description,owner_user_id,created_at,updated_at "
                "FROM teams WHERE status='active' ORDER BY name,team_id"
            ).fetchall()
            assignments = self._active_assignments(connection, user_id, None, timestamp)
        result: list[dict[str, object]] = []
        for row in teams:
            team_id = str(row["team_id"])
            visible = evaluate(assignments, team_node(team_id, "read"), now=timestamp).allowed or any(
                item.is_active(now=timestamp)
                and item.node.startswith(f"team.{team_id}.")
                and evaluate(assignments, item.node, now=timestamp).allowed
                for item in assignments
            )
            if visible:
                data = dict(row)
                data["membership_status"] = "permission_derived"
                result.append(data)
        return result

    def team_members(self, team_id: str) -> list[dict[str, object]]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """SELECT DISTINCT u.github_user_id,
                                   u.login_snapshot,
                                   u.status,
                                   g.source_type,
                                   g.source_id,
                                   g.created_at AS joined_at
                   FROM grants g
                            JOIN users u ON u.github_user_id = g.github_user_id
                   WHERE g.team_id = ?
                     AND g.status = 'active'
                   ORDER BY u.login_snapshot, u.github_user_id""",
                (team_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def system_catalog(self) -> list[dict[str, object]]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT node AS value,description,active FROM permission_nodes ORDER BY node").fetchall()
        return [dict(row) for row in rows]

    def user_system_permissions(self, user_id: str) -> list[dict[str, object]]:
        """用户的系统级节点（直属 ∪ 模板套用展开）。

        系统级 = `team_id IS NULL` 的直属/首登模板授权，加上 System Team 作用域的模板授权
        （`system-template:<template>:<user>`，新用户注册时写在这）。不加节点前缀过滤：
        编辑器展示的是完整权限目录，只按前缀筛会让"能勾但不回显"的节点在下次保存时被
        静默丢掉；模板套用节点存在 `template_assignments`，只查 `grant_assignments` 会
        让这类权限在编辑器里完全消失。
        """
        with self.database.transaction() as connection:
            rows = connection.execute(
                """SELECT ga.node AS value, ga.effect, g.source_type, g.source_id, g.created_at
                   FROM grant_assignments ga
                   JOIN grants g ON g.grant_id = ga.grant_id
                   WHERE g.github_user_id = ?
                     AND (g.team_id IS NULL OR g.team_id = ?)
                     AND g.status = 'active'
                   UNION ALL
                   SELECT ta.node AS value, ta.effect, g.source_type, g.source_id, g.created_at
                   FROM template_assignments ta
                   JOIN grants g ON g.template_id = ta.template_id
                   WHERE g.github_user_id = ?
                     AND (g.team_id IS NULL OR g.team_id = ?)
                     AND g.status = 'active'
                     AND g.template_id IS NOT NULL
                   ORDER BY value, source_type""",
                (user_id, SYSTEM_TEAM_ID, user_id, SYSTEM_TEAM_ID),
            ).fetchall()
        seen: dict[str, dict[str, object]] = {}
        for row in rows:
            if row["value"] in seen:
                continue
            seen[row["value"]] = {
                "value": row["value"],
                "effect": row["effect"],
                "granted_by": row["source_id"],
                "source_type": row["source_type"],
                "created_at": row["created_at"],
            }
        return list(seen.values())

    def user_permission_assignments(self, user_id: str, *, now: int | None = None) -> list[dict[str, object]]:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            assignments = self._active_assignments(connection, user_id, None, timestamp)
        return [
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
        ]

    def set_user_system_permissions(self, user_id: str, permissions: dict[str, str], *,
                                    granted_by: str,
                                    now: int | None = None) -> list[dict[str, object]]:
        """写入用户的系统级节点集合（整体替换）。

        模板实例也是节点，因此不需要单独的模板参数：提交里出现模板节点就存成节点，
        求值时展开成模板成员节点。
        """
        timestamp = int(time.time()) if now is None or now <= 0 else now
        assignments = [
            PermissionAssignment(
                assignment_id=f"direct:{user_id}:{index}",
                node=permission_to_node(value),
                effect=effect,
                source_type="direct",
                source_id=granted_by,
            )
            for index, (value, effect) in enumerate(sorted(permissions.items()))
        ]
        if any(item.effect not in {"allow", "deny"} for item in assignments):
            raise ApiError(400, "invalid_request", "permission effect must be allow or deny")
        with self.database.transaction() as connection:
            grant_id = f"direct-system:{user_id}"
            connection.execute(
                """INSERT INTO grants
                   (grant_id, github_user_id, source_type, source_id, team_id, status, created_by, created_at,
                    updated_at)
                   VALUES (?, ?, 'direct', ?, NULL, 'active', ?, ?, ?)
                   ON CONFLICT(grant_id) DO UPDATE SET updated_at=excluded.updated_at,
                                                       created_by=excluded.created_by""",
                (grant_id, user_id, granted_by, granted_by, timestamp, timestamp),
            )
            connection.execute("DELETE FROM grant_assignments WHERE grant_id=?", (grant_id,))
            connection.executemany(
                """INSERT INTO grant_assignments
                   (assignment_id, grant_id, node, effect, priority, grant_effect, source_type, source_id)
                   VALUES (?, ?, ?, ?, 0, 'allow', 'direct', ?)""",
                [(item.assignment_id, grant_id, item.node, item.effect, granted_by) for item in assignments],
            )
        return self.user_system_permissions(user_id)

    def team_authorization(self, team_id: str) -> dict[str, object]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """SELECT g.grant_id, g.github_user_id, ga.node, ga.effect, ga.priority, ga.grant_effect
                   FROM grants g
                            JOIN grant_assignments ga ON ga.grant_id = g.grant_id
                   WHERE g.team_id = ?
                   ORDER BY g.github_user_id, ga.node""",
                (team_id,),
            ).fetchall()
        return {"team_id": team_id, "assignments": [dict(row) for row in rows]}

    def set_tester_scopes(self, team_id: str, user_id: str, package_ids: list[str], *, granted_by: str,
                          now: int | None = None) -> list[str]:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        with self.database.transaction() as connection:
            grant_id = f"tester-scope:{team_id}:{user_id}"
            connection.execute(
                """INSERT INTO grants
                   (grant_id, github_user_id, source_type, source_id, team_id, status, created_by, created_at,
                    updated_at)
                   VALUES (?, ?, 'tester_scope', ?, ?, 'active', ?, ?, ?)
                   ON CONFLICT(grant_id) DO UPDATE SET updated_at=excluded.updated_at,
                                                       created_by=excluded.created_by""",
                (grant_id, user_id, granted_by, team_id, granted_by, timestamp, timestamp),
            )
            connection.execute("DELETE FROM grant_assignments WHERE grant_id=?", (grant_id,))
            connection.executemany(
                """INSERT INTO grant_assignments
                   (assignment_id, grant_id, node, effect, priority, grant_effect, source_type, source_id)
                   VALUES (?, ?, ?, ?, 0, 'allow', 'tester_scope', ?)""",
                [
                    (f"{grant_id}:{index}", grant_id, f"{package_resource_node(team_id, package_id)}.read", "allow",
                     granted_by)
                    for index, package_id in enumerate(sorted(set(package_ids)))
                ],
            )
        return sorted(set(package_ids))
