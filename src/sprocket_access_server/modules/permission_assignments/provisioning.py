from __future__ import annotations

from ...domain.permissions import team_node
from ..permission_templates.instance_nodes import template_instance_node


def template_nodes(connection, team_id: str, template_name: str) -> tuple[str, ...]:
    """模板的实例节点（单条）。授权记节点，模板内容变化在求值阶段生效。"""
    return (template_instance_node(connection, team_id, template_name),)


def template_exists(connection, team_id: str, template_id: str) -> bool:
    row = connection.execute(
        "SELECT template_id FROM permission_templates WHERE team_id=? AND template_id=? AND status='active'",
        (team_id, template_id.strip()),
    ).fetchone()
    return row is not None


def upsert_assignment_grant(
        connection,
        *,
        grant_id: str,
        user_id: str,
        team_id: str | None,
        nodes: tuple[str, ...],
        source_type: str,
        source_id: str,
        created_by: str,
        now: int,
) -> None:
    connection.execute(
        """INSERT INTO grants
           (grant_id, github_user_id, source_type, source_id, team_id, status, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
           ON CONFLICT(grant_id) DO UPDATE SET status='active',
                                               source_type=excluded.source_type,
                                               source_id=excluded.source_id,
                                               team_id=excluded.team_id,
                                               updated_at=excluded.updated_at,
                                               created_by=excluded.created_by""",
        (grant_id, user_id, source_type, source_id, team_id, created_by, now, now),
    )
    connection.execute("DELETE FROM grant_assignments WHERE grant_id=?", (grant_id,))
    connection.executemany(
        """INSERT INTO grant_assignments
           (assignment_id, grant_id, node, effect, priority, grant_effect, source_type, source_id)
           VALUES (?, ?, ?, ?, 0, 'allow', ?, ?)""",
        [
            (f"{grant_id}:{index}", grant_id, node, "allow", source_type, source_id)
            for index, node in enumerate(nodes)
        ],
    )
    connection.executemany(
        "INSERT OR IGNORE INTO permission_nodes(node) VALUES(?)",
        [(node,) for node in nodes],
    )


def upsert_template_grant(
        connection,
        *,
        grant_id: str,
        user_id: str,
        team_id: str,
        template_id: str,
        created_by: str,
        now: int,
) -> None:
    connection.execute(
        """INSERT INTO grants
           (grant_id, github_user_id, source_type, source_id, template_id, team_id, status, created_by,
            created_at, updated_at)
           VALUES (?, ?, 'permission_template', ?, ?, ?, 'active', ?, ?, ?)
           ON CONFLICT(grant_id) DO UPDATE SET status='active',
                                               source_id=excluded.source_id,
                                               template_id=excluded.template_id,
                                               team_id=excluded.team_id,
                                               updated_at=excluded.updated_at,
                                               created_by=excluded.created_by""",
        (grant_id, user_id, template_id, template_id, team_id, created_by, now, now),
    )


def revoke_team_assignments(connection, *, team_id: str, user_id: str, now: int) -> int:
    cursor = connection.execute(
        "UPDATE grants SET status='revoked',revoked_at=?,updated_at=? "
        "WHERE team_id=? AND github_user_id=? AND status='active'",
        (now, now, team_id, user_id),
    )
    return int(cursor.rowcount)
