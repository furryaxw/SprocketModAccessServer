from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid

from ...domain.errors import ApiError
from ...infrastructure.database import SQLiteDatabase
from ...infrastructure.utilities.team_ids import derive_team_id

logger = logging.getLogger(__name__)


class PlatformStore:
    def __init__(self, database: SQLiteDatabase, mode: str = "simple") -> None:
        if mode not in {"simple", "complex"}:
            raise ValueError("platform mode must be simple or complex")
        self.database, self.mode = database, mode

    def user(self, user_id: str) -> dict[str, object] | None:
        self.database.initialize()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM users WHERE github_user_id=?", (user_id,)).fetchone()
            return None if row is None else dict(row)

    def user_exists(self, user_id: str) -> bool:
        return self.user(user_id) is not None

    def users(
            self,
            *,
            limit: int = 100,
            offset: int = 0,
            login: str | None = None,
            display_name: str | None = None,
            user_id: str | None = None,
            status: str | None = None,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("user pagination is invalid")
        clauses: list[str] = []
        values: list[object] = []
        for column, value in (
                ("login_snapshot", login),
                ("display_name", display_name),
                ("github_user_id", user_id),
        ):
            if value:
                clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
                values.append(f"%{value.casefold()}%")
        if status:
            if status not in {"active", "suspended"}:
                raise ValueError("user status is invalid")
            clauses.append("status=?")
            values.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.transaction() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT github_user_id,login_snapshot,display_name,status,created_at,updated_at,last_login_at "
                f"FROM users{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*values, limit, offset))]

    def count_users(
            self,
            *,
            login: str | None = None,
            display_name: str | None = None,
            user_id: str | None = None,
            status: str | None = None,
    ) -> int:
        clauses: list[str] = []
        values: list[object] = []
        for column, value in (
                ("login_snapshot", login),
                ("display_name", display_name),
                ("github_user_id", user_id),
        ):
            if value:
                clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
                values.append(f"%{value.casefold()}%")
        if status:
            if status not in {"active", "suspended"}:
                raise ValueError("user status is invalid")
            clauses.append("status=?")
            values.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.transaction() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS total FROM users{where}", values,
            ).fetchone()
            return int(row["total"])

    def set_user_status(self, user_id: str, status: str, *, now: int) -> dict[str, object]:
        if status not in {"active", "suspended"}:
            raise ValueError("invalid user status")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE users SET status=?,updated_at=? WHERE github_user_id=?",
                (status, now, user_id))
            if cursor.rowcount != 1:
                raise ValueError("user was not found")
            return dict(connection.execute("SELECT * FROM users WHERE github_user_id=?", (user_id,)).fetchone())

    def teams(self) -> list[dict[str, object]]:
        with self.database.transaction() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT t.*,
                          (SELECT COUNT(DISTINCT g.github_user_id)
                           FROM grants g
                           WHERE g.team_id = t.team_id
                             AND g.status = 'active') AS member_count
                   FROM teams t
                   ORDER BY t.created_at DESC""")]

    def update_team(self, team_id: str, *, name: str, description: str, now: int) -> dict[str, object]:
        with self.database.transaction() as connection:
            cursor = connection.execute("UPDATE teams SET name=?,description=?,updated_at=? WHERE team_id=?",
                                        (name.strip(), description.strip(), now, team_id))
            if cursor.rowcount != 1:
                raise ValueError("Team was not found")
            return dict(connection.execute("SELECT * FROM teams WHERE team_id=?", (team_id,)).fetchone())

    def set_team_status(self, team_id: str, status: str, *, now: int) -> dict[str, object]:
        if status not in {"active", "suspended", "archived"}:
            raise ValueError("invalid Team status")
        with self.database.transaction() as connection:
            cursor = connection.execute("UPDATE teams SET status=?,updated_at=? WHERE team_id=?",
                                        (status, now, team_id))
            if cursor.rowcount != 1:
                raise ValueError("Team was not found")
            return dict(connection.execute("SELECT * FROM teams WHERE team_id=?", (team_id,)).fetchone())

    def create_application(self, applicant: str, name: str, description: str, *,
                           now: int | None = None) -> dict[
        str, object]:
        if self.mode != "complex":
            raise ValueError("Team applications are unavailable in simple mode")
        timestamp = int(time.time()) if now is None or now <= 0 else now
        application_id = uuid.uuid4().hex
        # 申请预先占用将来要用的 team id（由名称派生），待审期间同名申请只允许一份。
        reserved_team_id = derive_team_id(name)
        self.database.initialize()
        with self.database.transaction() as connection:
            pending = connection.execute(
                "SELECT 1 FROM team_applications WHERE team_id=? AND status='pending'",
                (reserved_team_id,),
            ).fetchone()
            if pending is not None:
                raise ApiError(
                    409, "team_application_exists",
                    "a pending Team application with the same name already exists",
                )
            connection.execute(
                "INSERT INTO team_applications(application_id,applicant_user_id,name,team_id,description,created_at) VALUES (?,?,?,?,?,?)",
                (application_id, applicant, name.strip(), reserved_team_id, description.strip(), timestamp))
            row = connection.execute("SELECT * FROM team_applications WHERE application_id=?",
                                     (application_id,)).fetchone()
            return dict(row)

    def applications(self, *, status: str = "pending", limit: int = 50, offset: int = 0) -> tuple[list[dict[str, object]], int]:
        if status not in {"pending", "approved", "rejected"}:
            raise ValueError("application status is invalid")
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("application pagination is invalid")
        self.database.initialize()
        with self.database.transaction() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) AS total FROM team_applications WHERE status=?", (status,)
            ).fetchone()["total"])
            rows = connection.execute(
                """SELECT * FROM team_applications WHERE status=?
                   ORDER BY created_at LIMIT ? OFFSET ?""",
                (status, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows], total

    def reject_application(self, application_id: str, reviewer: str, reason: str, *, now: int) -> dict[str, object]:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE team_applications SET status='rejected', reviewed_by=?, rejection_reason=?, reviewed_at=? WHERE application_id=? AND status='pending'",
                (reviewer, reason.strip(), now, application_id))
            if cursor.rowcount != 1:
                raise ValueError("Team application is not pending")
            return {"application_id": application_id, "status": "rejected"}

    def invite(self, team_id: str, target_user: str, template_name: str, inviter: str, *, now: int,
               ttl: int = 7 * 86400) -> \
            dict[str, object]:
        if not template_name:
            raise ValueError("permission template name is required")
        token = secrets.token_urlsafe(32)
        invitation_id = uuid.uuid4().hex
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO team_invitations(invitation_id,token_hash,team_id,target_user_id,permission_template_name,invited_by,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (invitation_id, hashlib.sha256(token.encode()).hexdigest(), team_id, target_user, template_name,
                 inviter,
                 now + ttl, now))
        return {"invitation_id": invitation_id, "token": token, "expires_at": now + ttl,
                "permission_template_name": template_name}
