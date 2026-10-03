from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from ..permission_assignments.store import assignments_for_permissions
from ...domain.models import PermissionAssignment
from ...domain.resources import SYSTEM_TEAM_ID
from ...infrastructure.database import SQLiteDatabase
from ...infrastructure.events import ResourceChanged


@dataclass(frozen=True)
class GrantTemplate:
    template_id: str
    name: str
    permissions: frozenset[str]
    expires_in: int | None
    created_by: str
    created_at: int
    status: str = "active"
    assignments: tuple[PermissionAssignment, ...] = ()


class SQLiteGrantTemplateStore:
    def __init__(self, database: SQLiteDatabase, *, events=None):
        self.database = database
        self.events = events
        self.database.initialize()

    def create(
            self,
            *,
            name: str,
            permissions: frozenset[str],
            expires_in: int | None,
            created_by: str,
            team_id: str | None = None,
            now: int | None = None,
    ) -> GrantTemplate:
        name = name.strip()
        team_id = team_id or SYSTEM_TEAM_ID
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if not name or len(name) > 100 or not permissions or not created_by:
            raise ValueError("grant template is invalid")
        if expires_in is not None and not 1 <= expires_in <= 10 * 365 * 24 * 60 * 60:
            raise ValueError("grant template expiry is invalid")
        template = GrantTemplate(
            secrets.token_hex(12), name, frozenset(item.strip() for item in permissions),
            expires_in, created_by, timestamp,
        )
        if not template.permissions or any(not item for item in template.permissions):
            raise ValueError("grant template permissions are invalid")
        try:
            with self.database.transaction() as connection:
                assignments = assignments_for_permissions(
                    template.permissions, source_type="template", source_id=template.template_id
                )
                connection.execute(
                    "INSERT INTO permission_templates (template_id,name,template_kind,team_id,status,expires_in,created_by,created_at) VALUES (?,?, 'permission_template', ?, 'active', ?, ?, ?)",
                    (template.template_id, template.name, team_id, template.expires_in,
                     template.created_by, template.created_at),
                )
                connection.executemany(
                    "INSERT INTO template_assignments (assignment_id,template_id,node,effect,priority,grant_effect) VALUES (?,?,?,'allow',0,'allow')",
                    [(item.assignment_id, template.template_id, item.node) for item in assignments],
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO permission_nodes(node) VALUES (?)",
                    [(item.node,) for item in assignments],
                )
        except Exception as exc:
            raise ValueError("grant template name already exists") from exc
        self._publish_change(
            kind="create",
            action="manage",
            template_id=template.template_id,
            team_id=team_id,
            data={"name": template.name, "expires_in": template.expires_in},
        )
        return GrantTemplate(
            template.template_id,
            template.name,
            template.permissions,
            template.expires_in,
            template.created_by,
            template.created_at,
            template.status,
            assignments,
        )

    def get(self, template_id: str, *, active_only: bool = True, team_id: str | None = None) -> GrantTemplate | None:
        team_id = team_id or SYSTEM_TEAM_ID
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM permission_templates WHERE template_id = ? AND (? IS NULL OR team_id = ?)",
                (template_id, team_id, team_id)).fetchone()
        if row is None or (active_only and row["status"] != "active"):
            return None
        assignments = self._assignments(template_id)
        return GrantTemplate(row["template_id"], row["name"], frozenset(item.node for item in assignments),
                             row["expires_in"], row["created_by"], row["created_at"], row["status"], assignments)

    def _assignments(self, template_id: str) -> tuple[PermissionAssignment, ...]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM template_assignments WHERE template_id=? ORDER BY priority DESC,assignment_id",
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

    def list(self, *, active_only: bool = True, team_id: str | None = None) -> tuple[GrantTemplate, ...]:
        team_id = team_id or SYSTEM_TEAM_ID
        query = "SELECT * FROM permission_templates"
        if active_only:
            query += " WHERE status = 'active'"
        if team_id:
            query += " AND " if " WHERE " in query else " WHERE "
            query += "team_id = ?"
        query += " ORDER BY name, template_id"
        with self.database.transaction() as connection:
            rows = connection.execute(query, (team_id,) if team_id else ()).fetchall()
        return tuple(self.get(row["template_id"], active_only=False, team_id=team_id) for row in rows)

    def search(self, *, active_only: bool = True, team_id: str | None = None,
               limit: int = 50, offset: int = 0) -> tuple[tuple[GrantTemplate, ...], int]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("template pagination is invalid")
        team_id = team_id or SYSTEM_TEAM_ID
        clauses: list[str] = []
        values: list[object] = []
        if active_only:
            clauses.append("status = 'active'")
        if team_id:
            clauses.append("team_id = ?")
            values.append(team_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.database.transaction() as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) AS total FROM permission_templates{where}", values
            ).fetchone()["total"])
            rows = connection.execute(
                f"""SELECT template_id FROM permission_templates{where}
                    ORDER BY name, template_id LIMIT ? OFFSET ?""",
                (*values, limit, offset),
            ).fetchall()
        return tuple(
            self.get(row["template_id"], active_only=False, team_id=team_id)
            for row in rows
        ), total

    def set_status(self, template_id: str, status: str, *, team_id: str | None = None) -> None:
        team_id = team_id or SYSTEM_TEAM_ID
        if status not in {"active", "disabled"}:
            raise ValueError("grant template status is invalid")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE permission_templates SET status = ? WHERE template_id = ? AND (? IS NULL OR team_id = ?)",
                (status, template_id, team_id, team_id))
            if cursor.rowcount != 1:
                raise ValueError("grant template was not found")
        self._publish_change(
            kind="update",
            action="manage",
            template_id=template_id,
            team_id=team_id,
            data={"status": status},
        )

    def update(
            self,
            template_id: str,
            *,
            name: str,
            permissions: frozenset[str],
            expires_in: int | None,
            team_id: str | None = None,
    ) -> GrantTemplate:
        team_id = team_id or SYSTEM_TEAM_ID
        name = name.strip()
        if not name or len(name) > 100 or not permissions:
            raise ValueError("grant template is invalid")
        if expires_in is not None and not 1 <= expires_in <= 10 * 365 * 24 * 60 * 60:
            raise ValueError("grant template expiry is invalid")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM permission_templates WHERE template_id=? AND (? IS NULL OR team_id=?)",
                (template_id, team_id, team_id),
            ).fetchone()
            if row is None:
                raise ValueError("grant template was not found")
            try:
                assignments = assignments_for_permissions(
                    permissions, source_type="template", source_id=template_id
                )
                connection.execute(
                    "UPDATE permission_templates SET name=?,expires_in=? WHERE template_id=?",
                    (name, expires_in, template_id),
                )
                connection.execute("DELETE FROM template_assignments WHERE template_id=?", (template_id,))
                connection.executemany(
                    "INSERT INTO template_assignments (assignment_id,template_id,node,effect,priority,grant_effect) VALUES (?,?,?,'allow',0,'allow')",
                    [(item.assignment_id, template_id, item.node) for item in assignments],
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO permission_nodes(node) VALUES (?)",
                    [(item.node,) for item in assignments],
                )
            except Exception as exc:
                raise ValueError("grant template name already exists") from exc
            updated = connection.execute("SELECT * FROM permission_templates WHERE template_id=?",
                                         (template_id,)).fetchone()
        self._publish_change(
            kind="update",
            action="manage",
            template_id=template_id,
            team_id=team_id,
            data={"name": name, "expires_in": expires_in},
        )
        return GrantTemplate(updated["template_id"], updated["name"],
                             frozenset(item.node for item in assignments), updated["expires_in"],
                             updated["created_by"], updated["created_at"], updated["status"], assignments)

    def _publish_change(self, *, kind: str, action: str, template_id: str, team_id: str | None,
                        data: dict[str, object]) -> None:
        if self.events is None:
            return
        self.events.publish(ResourceChanged(
            kind=kind,
            node=f"team.{team_id or 'default'}.permission_templates",
            action=action,
            data={"template_id": template_id, **data},
            team_id=team_id,
        ))
