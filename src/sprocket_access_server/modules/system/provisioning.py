from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid

from ...domain.permissions import team_node
from ...domain.resources import DEFAULT_TEAM_ID, SYSTEM_TEAM_ID
from ...infrastructure.database import SQLiteDatabase
from ...infrastructure.utilities.team_ids import derive_team_id, unique_team_id
from ..permission_assignments.policy import (
    require_team_owner_assignment_change,
    require_team_owner_removal,
)
from ..permission_assignments.provisioning import (
    revoke_team_assignments,
    template_exists,
    template_nodes,
    upsert_assignment_grant,
    upsert_template_grant,
)
from ..permission_templates.provisioning import clone_template_team

logger = logging.getLogger(__name__)


class SystemProvisioning:
    def __init__(self, database: SQLiteDatabase, mode: str) -> None:
        self.database = database
        self.mode = mode

    def ensure_user(self, user_id: str, login: str, email: str = "", *, now: int | None = None) -> dict[str, object]:
        return ensure_user(self.database, self.mode, user_id, login, email, now=now)

    def user_exists(self, user_id: str) -> bool:
        self.database.initialize()
        with self.database.transaction() as connection:
            return connection.execute(
                "SELECT 1 FROM users WHERE github_user_id=?",
                (user_id,),
            ).fetchone() is not None

    def ensure_service_account(self, user_id: str, *, now: int | None = None) -> dict[str, object]:
        return ensure_service_account(self.database, user_id, now=now)

    def assign_system_template(
            self,
            user_id: str,
            template_id: str,
            *,
            created_by: str = "system",
            now: int | None = None,
    ) -> None:
        assign_system_template(
            self.database,
            user_id,
            template_id,
            created_by=created_by,
            now=now,
        )

    def set_user_permission_template(
            self,
            user_id: str,
            template_name: str,
            *,
            actor: str,
            now: int,
    ) -> dict[str, object]:
        return set_user_permission_template(
            self.database,
            user_id,
            template_name,
            actor=actor,
            now=now,
        )

    def update_user_profile(
            self,
            user_id: str,
            *,
            status: str | None,
            template_name: str | None,
            actor: str,
            now: int,
    ) -> dict[str, object]:
        return update_user_profile(
            self.database,
            user_id,
            status=status,
            template_name=template_name,
            actor=actor,
            now=now,
        )

    def approve_application(self, application_id: str, reviewer: str, *, now: int) -> dict[str, object]:
        return approve_application(self.database, application_id, reviewer, now=now)

    def preview_application_approval(self, application_id: str) -> dict[str, object]:
        return preview_application_approval(self.database, application_id)

    def accept_invitation(self, token: str, user_id: str, *, now: int) -> dict[str, object]:
        return accept_invitation(self.database, token, user_id, now=now)

    def set_member_permission_template(
            self,
            team_id: str,
            user_id: str,
            template_name: str,
            *,
            now: int,
    ) -> dict[str, object]:
        return set_member_permission_template(
            self.database,
            team_id,
            user_id,
            template_name,
            now=now,
        )

    def remove_member(self, team_id: str, user_id: str, *, now: int) -> dict[str, object]:
        return remove_member(self.database, team_id, user_id, now=now)


def ensure_service_account(database: SQLiteDatabase, user_id: str, *, now: int | None = None) -> dict[str, object]:
    """保留服务身份的 `users` 行。

    已存在的行不改写：管理员为该行建立的授权与状态由现有用户管理面维护，
    暂停该行即停止签发它的会话。
    """
    service_id = user_id.strip()
    if not service_id or service_id.isdigit():
        raise ValueError("service account id must be a non-numeric non-empty string")
    timestamp = int(time.time()) if now is None or now <= 0 else now
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO users(github_user_id, login_snapshot, display_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (service_id, service_id, service_id, timestamp, timestamp),
        )
        row = connection.execute(
            "SELECT * FROM users WHERE github_user_id=?", (service_id,)
        ).fetchone()
    logger.debug("service account ensured service_id=%s", service_id)
    return dict(row)


def ensure_user(
        database: SQLiteDatabase,
        mode: str,
        user_id: str,
        login: str,
        email: str = "",
        *,
        now: int | None = None,
) -> dict[str, object]:
    timestamp = int(time.time()) if now is None or now <= 0 else now
    database.initialize()
    with database.transaction() as connection:
        existing = connection.execute("SELECT github_user_id FROM users WHERE github_user_id=?", (user_id,)).fetchone()
        connection.execute(
            "INSERT INTO users(github_user_id, login_snapshot, display_name, email, created_at, updated_at, last_login_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(github_user_id) DO UPDATE SET login_snapshot=excluded.login_snapshot, display_name=excluded.display_name, email=CASE WHEN excluded.email != '' THEN excluded.email ELSE users.email END, updated_at=excluded.updated_at, last_login_at=excluded.last_login_at",
            (user_id, login, login, email.strip(), timestamp, timestamp, timestamp),
        )
        if mode == "simple":
            connection.execute(
                "INSERT OR IGNORE INTO teams(team_id,name,owner_user_id,created_by,created_at,updated_at) SELECT 'default', 'Default Team', github_user_id, github_user_id, ?, ? FROM users WHERE github_user_id = ?",
                (timestamp, timestamp, user_id),
            )
        owner = connection.execute("SELECT github_user_id FROM server_ownership WHERE singleton=1").fetchone()
        if mode == "simple" and owner and str(owner[0]) == user_id:
            logger.debug("default Team owner grant upsert user_id=%s team_id=%s", user_id, DEFAULT_TEAM_ID)
            upsert_assignment_grant(
                connection,
                grant_id=f"team-owner:{DEFAULT_TEAM_ID}:{user_id}",
                user_id=user_id,
                team_id=DEFAULT_TEAM_ID,
                nodes=(team_node(DEFAULT_TEAM_ID, "*"),),
                source_type="permission_template",
                source_id="Owner",
                created_by="system",
                now=timestamp,
            )
        row = connection.execute("SELECT * FROM users WHERE github_user_id = ?", (user_id,)).fetchone()
        logger.debug("platform user ensured user_id=%s login=%s existed=%s mode=%s",
                     user_id, login, existing is not None, mode)
        return dict(row)


def assign_system_template(
        database: SQLiteDatabase,
        user_id: str,
        template_id: str,
        *,
        created_by: str = "system",
        now: int | None = None,
) -> None:
    timestamp = int(time.time()) if now is None or now <= 0 else now
    normalized = template_id.strip()
    if not normalized:
        raise ValueError("permission template id is required")
    database.initialize()
    with database.transaction() as connection:
        if not template_exists(connection, SYSTEM_TEAM_ID, normalized):
            logger.warning("system template assignment failed missing template user_id=%s template_id=%s",
                           user_id, normalized)
            raise ValueError("permission template was not found")
        connection.execute(
            "INSERT OR IGNORE INTO users(github_user_id,created_at,updated_at) VALUES(?,?,?)",
            (user_id, timestamp, timestamp),
        )
        upsert_template_grant(
            connection,
            grant_id=f"system-template:{normalized}:{user_id}",
            user_id=user_id,
            team_id=SYSTEM_TEAM_ID,
            template_id=normalized,
            created_by=created_by,
            now=timestamp,
        )
        logger.debug("system template assigned user_id=%s template_id=%s created_by=%s",
                     user_id, normalized, created_by)


def set_user_permission_template(
        database: SQLiteDatabase,
        user_id: str,
        template_name: str,
        *,
        actor: str,
        now: int,
) -> dict[str, object]:
    if not template_name.strip():
        raise ValueError("permission template name is required")
    with database.transaction() as connection:
        target = connection.execute("SELECT github_user_id FROM users WHERE github_user_id=?",
                                    (user_id,)).fetchone()
        if target is None:
            raise ValueError("platform user was not found")
        upsert_assignment_grant(
            connection,
            grant_id=f"system-template:{user_id}",
            user_id=user_id,
            team_id=SYSTEM_TEAM_ID,
            nodes=template_nodes(connection, SYSTEM_TEAM_ID, template_name),
            source_type="permission_template",
            source_id=template_name,
            created_by=actor,
            now=now,
        )
        row = connection.execute("SELECT * FROM users WHERE github_user_id=?", (user_id,)).fetchone()
        return dict(row)


def update_user_profile(
        database: SQLiteDatabase,
        user_id: str,
        *,
        status: str | None,
        template_name: str | None,
        actor: str,
        now: int,
) -> dict[str, object]:
    """单事务更新系统用户的状态与权限模板，避免两段式保存只改一半。

    无外部请求可以直接调用；handler 层负责确认令牌与幂等。
    """
    if status is None and not template_name:
        raise ValueError("no user fields to update")
    if status is not None and status not in {"active", "suspended"}:
        raise ValueError("invalid user status")
    if template_name is not None and not template_name.strip():
        raise ValueError("permission template name is required")
    with database.transaction() as connection:
        target = connection.execute("SELECT github_user_id FROM users WHERE github_user_id=?",
                                    (user_id,)).fetchone()
        if target is None:
            raise ValueError("platform user was not found")
        if status is not None:
            cursor = connection.execute(
                "UPDATE users SET status=?,updated_at=? WHERE github_user_id=?",
                (status, now, user_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("platform user was not found")
        if template_name:
            upsert_assignment_grant(
                connection,
                grant_id=f"system-template:{user_id}",
                user_id=user_id,
                team_id=SYSTEM_TEAM_ID,
                nodes=template_nodes(connection, SYSTEM_TEAM_ID, template_name),
                source_type="permission_template",
                source_id=template_name,
                created_by=actor,
                now=now,
            )
        row = connection.execute("SELECT * FROM users WHERE github_user_id=?", (user_id,)).fetchone()
        return dict(row)


def approve_application(database: SQLiteDatabase, application_id: str, reviewer: str, *, now: int) -> dict[str, object]:
    with database.transaction() as connection:
        row = connection.execute("SELECT * FROM team_applications WHERE application_id=?",
                                 (application_id,)).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError("Team application is not pending")
        # team id 由名称派生（人类可读），同名冲突时追加 `-2`、`-3`。
        taken_team_ids = {str(item["team_id"]) for item in connection.execute("SELECT team_id FROM teams").fetchall()}
        team_id = unique_team_id(taken_team_ids, derive_team_id(str(row["name"])))
        connection.execute(
            "INSERT INTO teams(team_id,name,description,owner_user_id,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (team_id, row["name"], row["description"], row["applicant_user_id"],
             row["applicant_user_id"], now, now))
        clone_template_team(connection, target_team_id=team_id, actor=row["applicant_user_id"], now=now)
        upsert_assignment_grant(
            connection,
            grant_id=f"team-owner:{team_id}:{row['applicant_user_id']}",
            user_id=row["applicant_user_id"],
            team_id=team_id,
            nodes=(team_node(team_id, "*"),),
            source_type="permission_template",
            source_id="Owner",
            created_by=reviewer,
            now=now,
        )
        connection.execute(
            "UPDATE team_applications SET status='approved', reviewed_by=?, reviewed_at=? WHERE application_id=?",
            (reviewer, now, application_id))
        return {"application_id": application_id, "team_id": team_id, "status": "approved",
                "applicant_user_id": row["applicant_user_id"]}


def preview_application_approval(database: SQLiteDatabase, application_id: str) -> dict[str, object]:
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM team_applications WHERE application_id=?",
            (application_id,),
        ).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError("Team application is not pending")
        templates = connection.execute(
            "SELECT name, template_kind, expires_in FROM permission_templates WHERE team_id=? AND status='active' ORDER BY name",
            ("template",),
        ).fetchall()
    return {
        "application_id": application_id,
        "team": {
            "name": row["name"],
            "team_id": str(row["team_id"]),
            "description": row["description"],
            "owner_user_id": row["applicant_user_id"],
        },
        "owner_assignment": {
            "user_id": row["applicant_user_id"],
            "source_type": "permission_template",
            "source_id": "Owner",
            "node": "team.<new_team>.*",
        },
        "templates": [dict(template) for template in templates],
        "resources": [
            "team.<new_team>",
            "team.<new_team>.users",
            "team.<new_team>.permission_templates",
            "team.<new_team>.permission_assignments",
            "team.<new_team>.packages",
            "team.<new_team>.package_uploads",
            "team.<new_team>.keys",
        ],
        "workspace_option": {"kind": "team", "team_id": str(row["team_id"]), "name": row["name"]},
    }


def accept_invitation(database: SQLiteDatabase, token: str, user_id: str, *, now: int) -> dict[str, object]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with database.transaction() as connection:
        row = connection.execute("SELECT * FROM team_invitations WHERE token_hash=?", (token_hash,)).fetchone()
        if row is None or row["status"] != "pending" or int(row["expires_at"]) <= now or row[
            "target_user_id"] != user_id:
            raise ValueError("invitation is invalid or expired")
        template_name = row["permission_template_name"]
        upsert_assignment_grant(
            connection,
            grant_id=f"team-invite:{row['team_id']}:{user_id}",
            user_id=user_id,
            team_id=row["team_id"],
            nodes=template_nodes(connection, row["team_id"], template_name),
            source_type="permission_template",
            source_id=template_name,
            created_by=row["invited_by"],
            now=now,
        )
        connection.execute("UPDATE team_invitations SET status='accepted', accepted_at=? WHERE invitation_id=?",
                           (now, row["invitation_id"]))
        return {"team_id": row["team_id"], "permission_template_name": template_name, "status": "accepted"}


def set_member_permission_template(
        database: SQLiteDatabase,
        team_id: str,
        user_id: str,
        template_name: str,
        *,
        now: int,
) -> dict[str, object]:
    with database.transaction() as connection:
        team = connection.execute("SELECT owner_user_id FROM teams WHERE team_id=?", (team_id,)).fetchone()
        if team is None:
            raise ValueError("Team was not found")
        require_team_owner_assignment_change(
            owner_user_id=team["owner_user_id"],
            target_user_id=user_id,
            template_name=template_name,
        )
        upsert_assignment_grant(
            connection,
            grant_id=f"team-template:{team_id}:{user_id}",
            user_id=user_id,
            team_id=team_id,
            nodes=template_nodes(connection, team_id, template_name),
            source_type="permission_template",
            source_id=template_name,
            created_by="system",
            now=now,
        )
    with database.transaction() as connection:
        user = connection.execute(
            "SELECT github_user_id,login_snapshot,status FROM users WHERE github_user_id=?",
            (user_id,),
        ).fetchone()
    if user is None:
        raise ValueError("platform user was not found")
    return {
        **dict(user),
        "team_id": team_id,
        "permission_template": template_name,
    }


def remove_member(database: SQLiteDatabase, team_id: str, user_id: str, *, now: int) -> dict[str, object]:
    with database.transaction() as connection:
        team = connection.execute("SELECT owner_user_id FROM teams WHERE team_id=?", (team_id,)).fetchone()
        require_team_owner_removal(
            owner_user_id=team["owner_user_id"] if team is not None else None,
            target_user_id=user_id,
        )
        if revoke_team_assignments(connection, team_id=team_id, user_id=user_id, now=now) < 1:
            raise ValueError("active Team permission assignment was not found")
    return {"team_id": team_id, "user_id": user_id, "status": "removed", "removed_at": now}
