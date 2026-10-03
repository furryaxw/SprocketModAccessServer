from __future__ import annotations

from ...domain.resources import TEMPLATE_TEAM_ID


def clone_template_team(connection, *, target_team_id: str, actor: str, now: int) -> None:
    templates = connection.execute(
        "SELECT * FROM permission_templates WHERE team_id=? AND status='active'",
        (TEMPLATE_TEAM_ID,),
    ).fetchall()
    for template in templates:
        template_id = f"{target_team_id}:{template['template_id']}"
        connection.execute(
            """INSERT OR IGNORE INTO permission_templates
               (template_id, name, template_kind, team_id, status, expires_in, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (template_id, template["name"], template["template_kind"], target_team_id,
             template["status"], template["expires_in"], actor, now),
        )
        rows = connection.execute(
            "SELECT * FROM template_assignments WHERE template_id=?",
            (template["template_id"],),
        ).fetchall()
        connection.executemany(
            """INSERT OR IGNORE INTO template_assignments
               (assignment_id, template_id, node, effect, priority, grant_effect, starts_at, expires_at, revoked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    f"{template_id}:{index}",
                    template_id,
                    str(row["node"]).replace(f"team.{TEMPLATE_TEAM_ID}.", f"team.{target_team_id}."),
                    row["effect"],
                    row["priority"],
                    row["grant_effect"],
                    row["starts_at"],
                    row["expires_at"],
                    row["revoked_at"],
                )
                for index, row in enumerate(rows)
            ],
        )
