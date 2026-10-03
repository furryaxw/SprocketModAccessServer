from __future__ import annotations

import os
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .seed import load_template_seed

logger = logging.getLogger(__name__)

SCHEMA = """
         CREATE TABLE IF NOT EXISTS schema_migrations
         (
             version    INTEGER PRIMARY KEY,
             applied_at INTEGER NOT NULL
         );
         CREATE TABLE IF NOT EXISTS users
         (
             github_user_id TEXT PRIMARY KEY,
             login_snapshot TEXT    NOT NULL DEFAULT '',
             display_name   TEXT    NOT NULL DEFAULT '',
             email          TEXT    NOT NULL DEFAULT '',
             status         TEXT    NOT NULL DEFAULT 'active',
             created_at     INTEGER NOT NULL,
             updated_at     INTEGER NOT NULL,
             last_login_at  INTEGER
         );
         CREATE TABLE IF NOT EXISTS server_ownership
         (
             singleton      INTEGER PRIMARY KEY CHECK (singleton = 1),
             github_user_id TEXT    NOT NULL REFERENCES users (github_user_id),
             claimed_at     INTEGER NOT NULL
         );
         CREATE TABLE IF NOT EXISTS sessions
         (
             token_hash     TEXT PRIMARY KEY,
             github_user_id TEXT    NOT NULL REFERENCES users (github_user_id),
             created_at     INTEGER NOT NULL,
             expires_at     INTEGER NOT NULL,
             revoked_at     INTEGER
         );
         CREATE TABLE IF NOT EXISTS teams
         (
             team_id       TEXT PRIMARY KEY,
             name          TEXT    NOT NULL,
             description   TEXT    NOT NULL DEFAULT '',
             status        TEXT    NOT NULL DEFAULT 'active',
             owner_user_id TEXT    NOT NULL REFERENCES users (github_user_id),
             created_by    TEXT    NOT NULL REFERENCES users (github_user_id),
             created_at    INTEGER NOT NULL,
             updated_at    INTEGER NOT NULL
         );
         CREATE TABLE IF NOT EXISTS team_applications
         (
             application_id    TEXT PRIMARY KEY,
             applicant_user_id TEXT    NOT NULL REFERENCES users (github_user_id),
             name              TEXT    NOT NULL,
             team_id           TEXT    NOT NULL,
             description       TEXT    NOT NULL DEFAULT '',
             status            TEXT    NOT NULL DEFAULT 'pending',
             reviewed_by       TEXT,
             rejection_reason  TEXT,
             created_at        INTEGER NOT NULL,
             reviewed_at       INTEGER
         );
         CREATE UNIQUE INDEX IF NOT EXISTS idx_team_applications_pending_team_id ON team_applications (team_id) WHERE status = 'pending';
         CREATE TABLE IF NOT EXISTS team_invitations
         (
             invitation_id            TEXT PRIMARY KEY,
             token_hash               TEXT    NOT NULL UNIQUE,
             team_id                  TEXT    NOT NULL REFERENCES teams (team_id),
             target_user_id           TEXT    NOT NULL REFERENCES users (github_user_id),
             permission_template_name TEXT    NOT NULL,
             invited_by               TEXT    NOT NULL REFERENCES users (github_user_id),
             expires_at               INTEGER NOT NULL,
             status                   TEXT    NOT NULL DEFAULT 'pending',
             created_at               INTEGER NOT NULL,
             accepted_at              INTEGER
         );
         CREATE TABLE IF NOT EXISTS permission_nodes
         (
             node        TEXT PRIMARY KEY,
             description TEXT    NOT NULL DEFAULT '',
             active      INTEGER NOT NULL DEFAULT 1
         );
         CREATE TABLE IF NOT EXISTS permission_templates
         (
             template_id   TEXT PRIMARY KEY,
             name          TEXT    NOT NULL,
             template_kind TEXT    NOT NULL DEFAULT 'permission_template',
             team_id       TEXT,
             status        TEXT    NOT NULL DEFAULT 'active',
             expires_in    INTEGER,
             created_by    TEXT    NOT NULL,
             created_at    INTEGER NOT NULL,
             UNIQUE (team_id, name)
         );
         CREATE TABLE IF NOT EXISTS template_assignments
         (
             assignment_id TEXT PRIMARY KEY,
             template_id   TEXT    NOT NULL REFERENCES permission_templates (template_id),
             node          TEXT    NOT NULL,
             effect        TEXT    NOT NULL CHECK (effect IN ('allow', 'deny')),
             priority      INTEGER NOT NULL                                           DEFAULT 0,
             grant_effect  TEXT    NOT NULL CHECK (grant_effect IN ('allow', 'deny')) DEFAULT 'allow',
             starts_at     INTEGER,
             expires_at    INTEGER,
             revoked_at    INTEGER
         );
         CREATE TABLE IF NOT EXISTS grants
         (
             grant_id       TEXT PRIMARY KEY,
             github_user_id TEXT    NOT NULL REFERENCES users (github_user_id),
             source_type    TEXT    NOT NULL,
             source_id      TEXT    NOT NULL,
             template_id    TEXT REFERENCES permission_templates (template_id),
             team_id        TEXT,
             status         TEXT    NOT NULL CHECK (status IN ('active', 'revoked', 'suspended')) DEFAULT 'active',
             starts_at      INTEGER,
             expires_at     INTEGER,
             revoked_at     INTEGER,
             created_by     TEXT    NOT NULL,
             created_at     INTEGER NOT NULL,
             updated_at     INTEGER NOT NULL
         );
         CREATE TABLE IF NOT EXISTS grant_assignments
         (
             assignment_id TEXT PRIMARY KEY,
             grant_id      TEXT    NOT NULL REFERENCES grants (grant_id),
             node          TEXT    NOT NULL,
             effect        TEXT    NOT NULL CHECK (effect IN ('allow', 'deny')),
             priority      INTEGER NOT NULL                                           DEFAULT 0,
             grant_effect  TEXT    NOT NULL CHECK (grant_effect IN ('allow', 'deny')) DEFAULT 'allow',
             source_type   TEXT    NOT NULL,
             source_id     TEXT    NOT NULL,
             starts_at     INTEGER,
             expires_at    INTEGER,
             revoked_at    INTEGER
         );
         CREATE INDEX IF NOT EXISTS idx_grants_user ON grants (github_user_id, status);
         CREATE INDEX IF NOT EXISTS idx_grants_team ON grants (team_id, status);
         CREATE INDEX IF NOT EXISTS idx_grant_assignments_grant ON grant_assignments (grant_id);
         CREATE TABLE IF NOT EXISTS key_batches
         (
             batch_id         TEXT PRIMARY KEY,
             name             TEXT    NOT NULL,
             quantity         INTEGER NOT NULL,
             permissions_json TEXT    NOT NULL DEFAULT '[]',
             assignments_json TEXT    NOT NULL DEFAULT '[]',
             template_id      TEXT REFERENCES permission_templates (template_id),
             expires_at       INTEGER,
             created_by       TEXT    NOT NULL,
             created_at       INTEGER NOT NULL,
             team_id          TEXT
         );
         CREATE TABLE IF NOT EXISTS activation_keys
         (
             key_id                  TEXT PRIMARY KEY,
             batch_id                TEXT REFERENCES key_batches (batch_id),
             key_hash                TEXT    NOT NULL UNIQUE,
            key_plaintext           TEXT    NOT NULL DEFAULT '',
             key_prefix              TEXT,
             permissions_json        TEXT    NOT NULL DEFAULT '[]',
             assignments_json        TEXT    NOT NULL DEFAULT '[]',
             template_id             TEXT REFERENCES permission_templates (template_id),
             key_kind                TEXT    NOT NULL DEFAULT 'snapshot' CHECK (key_kind IN ('snapshot', 'template')),
             status                  TEXT    NOT NULL CHECK (status IN ('unused', 'redeemed', 'revoked')),
             redeemed_github_user_id TEXT,
             redeemed_at             INTEGER,
             delivered_to            TEXT,
             delivered_at            INTEGER,
             expires_at              INTEGER,
             note                    TEXT    NOT NULL DEFAULT '',
             created_at              INTEGER NOT NULL,
             team_id                 TEXT
         );
         CREATE INDEX IF NOT EXISTS idx_activation_keys_batch_id ON activation_keys (batch_id);
         CREATE INDEX IF NOT EXISTS idx_activation_keys_prefix ON activation_keys (key_prefix);
         CREATE TABLE IF NOT EXISTS packages
         (
             package_id    TEXT PRIMARY KEY,
             team_id       TEXT    NOT NULL,
             name          TEXT    NOT NULL,
             metadata_json TEXT    NOT NULL DEFAULT '{}',
             status        TEXT    NOT NULL DEFAULT 'published' CHECK (status IN ('published', 'disabled', 'unpublished')),
             created_at    INTEGER NOT NULL,
             updated_at    INTEGER NOT NULL
         );
         CREATE TABLE IF NOT EXISTS package_versions
         (
             package_id      TEXT    NOT NULL,
             version         TEXT    NOT NULL,
             permission_node TEXT    NOT NULL,
             archive_digest  TEXT    NOT NULL,
             archive_size    INTEGER NOT NULL,
             status          TEXT    NOT NULL CHECK (status IN ('published', 'disabled', 'unpublished')),
             metadata_json   TEXT    NOT NULL DEFAULT '{}',
             created_at      INTEGER NOT NULL,
             team_id         TEXT,
             PRIMARY KEY (package_id, version, team_id)
         );
         CREATE TABLE IF NOT EXISTS audit_events
         (
             id            INTEGER PRIMARY KEY AUTOINCREMENT,
             actor         TEXT    NOT NULL,
             action        TEXT    NOT NULL,
             target        TEXT    NOT NULL,
             metadata_json TEXT    NOT NULL,
             created_at    INTEGER NOT NULL,
             team_id       TEXT
         );
         CREATE TABLE IF NOT EXISTS idempotency_records
         (
             scope         TEXT    NOT NULL,
             request_key   TEXT    NOT NULL,
             request_hash  TEXT    NOT NULL,
             response_json TEXT    NOT NULL,
             created_at    INTEGER NOT NULL,
             PRIMARY KEY (scope, request_key)
         );
         CREATE TABLE IF NOT EXISTS upload_drafts
         (
             upload_id       TEXT PRIMARY KEY,
             object_key      TEXT    NOT NULL UNIQUE,
             package_id      TEXT    NOT NULL,
             version         TEXT    NOT NULL,
             permission_node TEXT    NOT NULL,
             size            INTEGER NOT NULL,
             content_type    TEXT    NOT NULL,
             metadata_json   TEXT    NOT NULL DEFAULT '{}',
             status          TEXT    NOT NULL CHECK (status IN ('pending', 'confirmed', 'expired')),
             expires_at      INTEGER NOT NULL,
             created_at      INTEGER NOT NULL,
             confirmed_at    INTEGER,
             team_id         TEXT
         );
         CREATE TABLE IF NOT EXISTS confirmation_tokens
         (
             token_hash TEXT PRIMARY KEY,
             action     TEXT    NOT NULL,
             target     TEXT    NOT NULL,
             created_by TEXT    NOT NULL,
             created_at INTEGER NOT NULL,
             expires_at INTEGER NOT NULL,
             used_at    INTEGER
         );
         CREATE TABLE IF NOT EXISTS email_outbox
         (
             message_id   TEXT PRIMARY KEY,
             recipient    TEXT    NOT NULL,
             subject      TEXT    NOT NULL,
             body         TEXT    NOT NULL,
             status       TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
             attempts     INTEGER NOT NULL DEFAULT 0,
             available_at INTEGER NOT NULL,
             created_at   INTEGER NOT NULL,
             sent_at      INTEGER,
             last_error   TEXT
         );
         CREATE INDEX IF NOT EXISTS idx_email_outbox_pending ON email_outbox (status, available_at); \
         """

CURRENT_SCHEMA_VERSION = 1


class SQLiteDatabase:
    def __init__(self, path: Path):
        self.path = path.expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.transaction() as connection:
            existing = {row[0] for row in
                        connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "schema_migrations" in existing:
                versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()]
                if versions and max(versions) != CURRENT_SCHEMA_VERSION:
                    logger.error("database schema mismatch path=%s current=%s expected=%s",
                                 self.path, max(versions), CURRENT_SCHEMA_VERSION)
                    raise RuntimeError("database schema version mismatch; clean reset required")
            connection.executescript(SCHEMA)
            for version in range(1, CURRENT_SCHEMA_VERSION + 1):
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, strftime('%s','now'))",
                    (version,),
                )
            self._seed_permission_templates(connection)
            self._repair_zero_timestamps(connection)
            logger.debug("database initialized path=%s schema_version=%d", self.path, CURRENT_SCHEMA_VERSION)

    @staticmethod
    def _repair_zero_timestamps(connection: sqlite3.Connection) -> None:
        timestamp = int(time.time())
        for table, columns in {
            "users": ("created_at", "updated_at"),
            "server_ownership": ("claimed_at",),
            "sessions": ("created_at",),
            "teams": ("created_at", "updated_at"),
            "team_applications": ("created_at",),
            "team_invitations": ("created_at",),
            "permission_templates": ("created_at",),
            "grants": ("created_at", "updated_at"),
            "key_batches": ("created_at",),
            "activation_keys": ("created_at",),
            "package_versions": ("created_at",),
            "audit_events": ("created_at",),
            "idempotency_records": ("created_at",),
            "upload_drafts": ("created_at",),
            "confirmation_tokens": ("created_at",),
            "email_outbox": ("available_at", "created_at"),
        }.items():
            for column in columns:
                connection.execute(
                    f"UPDATE {table} SET {column}=? WHERE {column}<=0",
                    (timestamp,),
                )

    @staticmethod
    def _seed_permission_templates(connection: sqlite3.Connection) -> None:
        timestamp = int(time.time())
        connection.execute(
            "INSERT OR IGNORE INTO users(github_user_id,login_snapshot,display_name,created_at,updated_at) VALUES('system','system','System',?,?)",
            (timestamp, timestamp),
        )
        for team_id, name in (
                ("system", "System"),
                ("template", "Template Team"),
        ):
            connection.execute(
                """INSERT OR IGNORE INTO teams
                   (team_id, name, description, owner_user_id, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, 'system', 'system', ?, ?)""",
                (team_id, name, f"Reserved {name} workspace", timestamp, timestamp),
            )
        for item in load_template_seed("system"):
            template_id = str(item["template_id"])
            name = str(item["name"])
            description = str(item.get("description", ""))
            nodes = item.get("nodes", [item.get("node")])
            if not isinstance(nodes, list) or not nodes or not all(isinstance(node, str) and node for node in nodes):
                raise RuntimeError(f"system template seed has invalid nodes: {template_id}")
            team_id = "system"
            connection.execute(
                "INSERT OR IGNORE INTO permission_templates(template_id,name,template_kind,team_id,created_by,created_at) VALUES(?,?, 'permission_template', ?, 'system', strftime('%s','now'))",
                (template_id, name, team_id),
            )
            for index, node in enumerate(nodes):
                connection.execute(
                    """INSERT OR IGNORE INTO template_assignments
                           (assignment_id, template_id, node, effect, priority, grant_effect)
                       VALUES (?, ?, ?, 'allow', 0, 'allow')""",
                    (f"{template_id}:{index}", template_id, node),
                )
                connection.execute("INSERT OR IGNORE INTO permission_nodes(node,description) VALUES(?,?)",
                                   (node, description))
        for item in load_template_seed("team"):
            template_id = str(item["template_id"])
            name = str(item["name"])
            description = str(item.get("description", ""))
            nodes = item.get("nodes")
            if not isinstance(nodes, list) or not nodes or not all(isinstance(node, str) for node in nodes):
                raise RuntimeError(f"team template seed has invalid nodes: {template_id}")
            connection.execute(
                "INSERT OR IGNORE INTO permission_templates(template_id,name,template_kind,team_id,created_by,created_at) VALUES(?,?, 'permission_template', ?, 'system', strftime('%s','now'))",
                (template_id, name, "template"),
            )
            for index, node in enumerate(nodes):
                connection.execute(
                    """INSERT OR IGNORE INTO template_assignments
                           (assignment_id, template_id, node, effect, priority, grant_effect)
                       VALUES (?, ?, ?, 'allow', 0, 'allow')""",
                    (f"{template_id}:{index}", template_id, node),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO permission_nodes(node,description) VALUES(?,?)",
                    (node, description),
                )

    @property
    def schema_version(self) -> int:
        self.initialize()
        with self.transaction() as connection:
            row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
            return int(row["version"] or 0)

    def integrity_check(self) -> bool:
        self.initialize()
        with self.transaction() as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            return bool(row and str(row[0]).lower() == "ok")

    def claim_first_owner(self, github_user_id: str, *, now: int,
                          owner_template_id: str = "template.owner") -> bool:
        if not github_user_id:
            raise ValueError("github user id is required")
        self.initialize()
        with self.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO users(github_user_id,created_at,updated_at) VALUES(?,?,?)",
                               (github_user_id, now, now))
            cursor = connection.execute(
                "INSERT OR IGNORE INTO server_ownership(singleton,github_user_id,claimed_at) VALUES(1,?,?)",
                (github_user_id, now))
            if cursor.rowcount == 1:
                connection.execute(
                    "INSERT OR IGNORE INTO grants(grant_id,github_user_id,source_type,source_id,template_id,status,created_by,created_at,updated_at) VALUES(?,?,'permission_template',?,?, 'active','system',?,?)",
                    (f"template-owner:{github_user_id}", github_user_id, owner_template_id, owner_template_id, now, now),
                )
                logger.debug("first owner claimed user_id=%s template_id=%s", github_user_id, owner_template_id)
            else:
                logger.debug("first owner claim skipped existing_owner=true user_id=%s", github_user_id)
            return cursor.rowcount == 1

    def owner_id(self) -> str | None:
        self.initialize()
        with self.transaction() as connection:
            row = connection.execute("SELECT github_user_id FROM server_ownership WHERE singleton=1").fetchone()
            return None if row is None else str(row[0])

    def backup_to(self, destination: Path) -> Path:
        self.initialize()
        if not self.integrity_check():
            raise ValueError("source database failed integrity check")
        target = destination.expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        try:
            if temporary.exists():
                temporary.unlink()
            source = self.connect()
            try:
                backup = sqlite3.connect(temporary)
                try:
                    source.backup(backup)
                finally:
                    backup.close()
            finally:
                source.close()
            if not self.verify_backup(temporary):
                raise ValueError("backup database failed integrity check")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

    @staticmethod
    def verify_backup(path: Path) -> bool:
        candidate = path.expanduser()
        if not candidate.is_file():
            return False
        try:
            connection = sqlite3.connect(candidate)
            try:
                row = connection.execute("PRAGMA integrity_check").fetchone()
                return bool(row and str(row[0]).lower() == "ok")
            finally:
                connection.close()
        except sqlite3.DatabaseError:
            return False

    @classmethod
    def restore_from(cls, backup: Path, destination: Path) -> Path:
        if not cls.verify_backup(backup):
            raise ValueError("backup database failed integrity check")
        target = destination.expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.restore-{os.getpid()}")
        try:
            if temporary.exists():
                temporary.unlink()
            source = sqlite3.connect(backup.expanduser())
            try:
                target_connection = sqlite3.connect(temporary)
                try:
                    source.backup(target_connection)
                finally:
                    target_connection.close()
            finally:
                source.close()
            if not cls.verify_backup(temporary):
                raise ValueError("restored database failed integrity check")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
