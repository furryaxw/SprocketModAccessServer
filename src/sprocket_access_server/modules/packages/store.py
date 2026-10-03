from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ...domain.resources import DEFAULT_TEAM_ID, SYSTEM_TEAM_ID, package_resource_node, split_package_id
from ...infrastructure.database import SQLiteDatabase
from ...infrastructure.events import ResourceChanged
from ...infrastructure.security.signing import sign_manifest
from .files import PAYLOAD_DLL, PAYLOAD_KIND_KEY, PAYLOAD_ZIP
from .metadata import validate_metadata

# 条目直接采用公开注册表的 schema 版本：私有侧不再另立形状，只有条目级 signature 是私有加法。
PUBLIC_SCHEMA_VERSION = 3

# 条目顶层允许出现的字段：其余一律只作为 `metadata` 子对象输出。
# `repository` 与 `install` 不在默认表里：前者未知时不出现（公开 v3 只收 <owner>/<name>），
# 后者必须由包显式声明（文件/载荷规则），没有默认值。
ENTRY_DEFAULTS: dict[str, object] = {
    "name": "",
    "authors": [],
    "license": "Private distribution",
    "display_name": {},
    "description": {},
    "dependencies": [],
    "recommendations": [],
    "category": "other",
    "tags": [],
}


def _iso_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _download_url(base: str, package_id: str) -> str:
    """下载端点的绝对 URL；`base` 是服务端自己的 origin（无尾斜杠）。"""
    path = f"/v1/packages/{package_id}/download"
    return f"{base.rstrip('/')}{path}" if base.strip() else path


def _entry_fields(metadata: dict[str, object], *, name: str) -> dict[str, object]:
    """条目顶层字段：只取白名单键，并把任何不符合 contract 的值归一化。

    写入路径已严格拒绝不合规元数据；这里是构建侧的安全网（历史数据、直接调用 store 的路径），
    保证发出去的每个条目都合法 —— 一个坏条目不只坏它自己，客户端会拒整份索引。
    """
    fields: dict[str, object] = {}
    for key, default in ENTRY_DEFAULTS.items():
        fields[key] = metadata.get(key, default)
    if not _ENTRY_NAME.fullmatch(str(fields["name"] or "")):
        fields["name"] = name
    if not fields["display_name"]:
        # registry 的 localized 定义要求至少一个键，因此不能留空对象。
        fields["display_name"] = {"en": str(fields["name"])}
    if not fields["description"]:
        fields["description"] = {"en": "Private package"}
    if not isinstance(fields["license"], str) or not fields["license"].strip():
        fields["license"] = ENTRY_DEFAULTS["license"]
    if str(fields["category"]) not in _CATEGORIES:
        fields["category"] = "other"
    fields["authors"] = [item for item in _string_list(fields["authors"]) if item]
    if not fields["authors"]:
        # 公开 v3 的 authors 出现就至少一项：没有作者时省略这个键。
        fields.pop("authors")
    # 仓库是 GitHub 的 `<owner>/<name>`；不合法或未知时省略这个键（v3 不收 null）。
    repository = metadata.get("repository")
    if isinstance(repository, str) and _REPOSITORY.fullmatch(repository.strip()):
        fields["repository"] = repository.strip()
    fields["tags"] = [item for item in _string_list(fields["tags"]) if item]
    fields["recommendations"] = [
        item for item in _string_list(fields["recommendations"]) if _PACKAGE_ID.fullmatch(item)
    ]
    fields["dependencies"] = [
        item for item in fields["dependencies"] if isinstance(item, dict) and {"id", "version", "when"} <= set(item)
    ] if isinstance(fields["dependencies"], list) else []
    install = metadata.get("install")
    if isinstance(install, dict):
        fields["install"] = _normalize_install(install)
    # 没有声明安装规则时条目不带 `install`：发布路径本就要求规则。
    return fields


def _normalize_install(install: dict[str, object]) -> dict[str, object]:
    """条目的 `install`：typed 规则与公开 schema 要求的伴随键原样带出。

    `files` 配 `scan_dlls`/`exclude`，`payload` 配 `exclude` 且不带 `scan_dlls` —— 互斥由写入校验保证。
    """
    normalized: dict[str, object] = {}
    for key in ("files", "payload", "replace"):
        if key in install:
            normalized[key] = install[key]
    if "scan_dlls" in install:
        normalized["scan_dlls"] = bool(install["scan_dlls"])
    if "exclude" in install:
        normalized["exclude"] = _string_list(install["exclude"])
    if str(install.get("mode", "")) in {"standard", "patch"}:
        normalized["mode"] = str(install["mode"])
    return normalized


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _release_dependencies(dependencies: object) -> list[dict[str, object]]:
    """发布级依赖：公开 release 形状只有 `{id, version}`，包级的 `when` 不外泄。"""
    if not isinstance(dependencies, list):
        return []
    return [
        {"id": item["id"], "version": item["version"]}
        for item in dependencies
        if isinstance(item, dict) and {"id", "version"} <= set(item)
    ]


def _version_from_row(row: Any) -> PackageVersion:
    raw_metadata = json.loads(row["metadata_json"] or "{}")
    # 载荷形态记在版本元数据里：它决定资产扩展名，但不属于包元数据，读出即摘除。
    kind = str(raw_metadata.pop(PAYLOAD_KIND_KEY, PAYLOAD_ZIP))
    return PackageVersion(
        row["package_id"], row["version"], row["permission_node"],
        row["archive_digest"], row["archive_size"], row["status"], row["created_at"],
        raw_metadata, row["team_id"], kind,
    )


def _package_record(row: Any, version_rows: list[Any]) -> PackageRecord:
    return PackageRecord(
        str(row["package_id"]), str(row["team_id"]), str(row["name"]),
        json.loads(row["metadata_json"] or "{}"), str(row["status"]),
        int(row["created_at"]), int(row["updated_at"]),
        tuple(_version_from_row(version) for version in version_rows),
    )

_PACKAGE_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
_ENTRY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CATEGORIES = frozenset({"gameplay", "utility", "library", "visual", "audio", "translation", "other"})
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
_CHANNEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
IMMUTABLE_METADATA_FIELDS = frozenset({
    "archive_digest",
    "archive_size",
    "digest",
    "id",
    "package_id",
    "payload_kind",
    "releases",
    "schema_version",
    "sha256",
    "size",
    "version",
})


@dataclass(frozen=True)
class PackageVersion:
    package_id: str
    version: str
    permission_node: str
    archive_digest: str
    archive_size: int
    status: str
    created_at: int
    metadata: dict[str, object] = field(default_factory=dict)
    team_id: str | None = None
    # 载荷形态，决定资产扩展名。
    payload_kind: str = PAYLOAD_ZIP

    def release(self, *, release_id: int = 1, dependencies: object = (),
                download_base_url: str = "") -> dict[str, object]:
        """一个版本在索引里的 release 条目：公开 release 形状。

        release 只承载"哪个版本、哪个资产"，依赖取公开发布级形状 `{id, version}`。
        """
        suffix = PAYLOAD_DLL if self.payload_kind == PAYLOAD_DLL else PAYLOAD_ZIP
        return {
            "id": release_id,
            "tag": self.version,
            "version": self.version,
            "prerelease": "-" in self.version,
            "published_at": _iso_time(self.created_at),
            "assets": [{
                "id": 1,
                "name": f"{self.package_id}.{suffix}",
                "size": self.archive_size,
                "download_url": _download_url(download_base_url, self.package_id),
                "digest": f"sha256:{self.archive_digest}",
                "updated_at": _iso_time(self.created_at),
            }],
            "dependencies": _release_dependencies(dependencies),
            "compatibility": {"source": "declared"},
        }

    def manifest(self, *, release_id: int = 1, dependencies: object = None,
                 download_base_url: str = "") -> dict[str, object]:
        entry = _entry_fields(self.metadata, name=split_package_id(self.package_id)[1])
        entry["id"] = self.package_id
        entry["schema_version"] = PUBLIC_SCHEMA_VERSION
        declared = entry["dependencies"] if dependencies is None else [
            dict(item) for item in dependencies if isinstance(item, dict)
        ]
        entry["releases"] = [
            self.release(release_id=release_id, dependencies=declared, download_base_url=download_base_url)
        ]
        return entry

    def signed_manifest(self, private_key, key_id: str, *, release_id: int = 1, dependencies: object = None,
                        download_base_url: str = "") -> dict[str, object]:
        return sign_manifest(
            self.manifest(release_id=release_id, dependencies=dependencies, download_base_url=download_base_url),
            private_key, key_id,
        )


@dataclass(frozen=True)
class PackageRecord:
    """包实体：元数据与状态属于包，归档属于版本。"""

    package_id: str
    team_id: str
    name: str
    metadata: dict[str, object]
    status: str
    created_at: int
    updated_at: int
    versions: tuple[PackageVersion, ...] = ()

    def entry(self, *, download_base_url: str = "") -> dict[str, object]:
        entry = _entry_fields(self.metadata, name=self.name or split_package_id(self.package_id)[1])
        entry["id"] = self.package_id
        entry["schema_version"] = PUBLIC_SCHEMA_VERSION
        # 安装规则与依赖都属于包：每个 release 带同一套声明。
        declared = entry["dependencies"]
        # 一包一条目：每个已发布版本一条 release，条目内序号单调递增。
        entry["releases"] = [
            version.release(release_id=index + 1, dependencies=declared, download_base_url=download_base_url)
            for index, version in enumerate(self.versions)
        ]
        return entry

    def signed_entry(self, private_key, key_id: str, *, download_base_url: str = "") -> dict[str, object]:
        return sign_manifest(self.entry(download_base_url=download_base_url), private_key, key_id)


class SQLitePackageStore:
    def __init__(self, database: SQLiteDatabase, *, events=None):
        self.database = database
        self.events = events
        self.database.initialize()

    def publish(self, package: PackageVersion, *, now: int | None = None, team_id: str | None = None) -> None:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if not _PACKAGE_ID.fullmatch(package.package_id) or not _SEMVER.fullmatch(package.version):
            raise ValueError("package identity is invalid")
        owner_team_id = team_id if team_id is not None else package.team_id or DEFAULT_TEAM_ID
        if owner_team_id == SYSTEM_TEAM_ID:
            raise ValueError("System workspace cannot publish Packages")
        # 包 id 的第一段必须是所属 Team：id 自带归属，跨 Team 不可能重名。
        if split_package_id(package.package_id)[0] != owner_team_id:
            raise ValueError("package ID must start with its Team id")
        permission_node = package_resource_node(owner_team_id, package.package_id)
        if package.archive_size < 1:
            raise ValueError("package metadata is invalid")
        validate_metadata(package.metadata)
        # 版本行额外记录载荷形态：它只用于重建资产扩展名，不进入包元数据。
        version_metadata = {**package.metadata, PAYLOAD_KIND_KEY: package.payload_kind}
        with self.database.transaction() as connection:
            try:
                connection.execute(
                    """INSERT INTO package_versions
                       (package_id, version, permission_node, archive_digest,
                        archive_size, status, metadata_json, created_at, team_id)
                       VALUES (?, ?, ?, ?, ?, 'published', ?, ?, ?)""",
                    (
                        package.package_id, package.version, permission_node,
                        package.archive_digest, package.archive_size,
                        json.dumps(version_metadata, ensure_ascii=False),
                        timestamp, owner_team_id,
                    ),
                )
                nodes = (
                    f"{permission_node}.read",
                    f"{permission_node}.manage",
                    f"{permission_node}.download",
                    f"{permission_node}.grant",
                )
                # 包实体随首个版本建立；已有实体不被版本元数据覆盖（元数据属于包）。
                metadata_name = str(package.metadata.get("name", "")).strip()
                if not _ENTRY_NAME.fullmatch(metadata_name):
                    metadata_name = split_package_id(package.package_id)[1]
                connection.execute(
                    """INSERT INTO packages
                           (package_id, team_id, name, metadata_json, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'published', ?, ?)
                       ON CONFLICT(package_id) DO UPDATE SET updated_at=excluded.updated_at""",
                    (
                        package.package_id, owner_team_id, metadata_name,
                        json.dumps(package.metadata, ensure_ascii=False), timestamp, timestamp,
                    ),
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO permission_nodes(node) VALUES (?)",
                    [(node,) for node in nodes],
                )
            except Exception as exc:
                raise ValueError(f"package version already exists or is invalid: {exc}") from exc
        self._publish_change(
            kind="create",
            action="manage",
            package_id=package.package_id,
            team_id=owner_team_id,
            data={"version": package.version, "status": "published"},
        )

    def get(self, package_id: str, version: str, *, team_id: str | None = None) -> PackageVersion | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM package_versions WHERE package_id = ? AND version = ? AND (? IS NULL OR team_id = ?)",
                (package_id, version, team_id, team_id),
            ).fetchone()
        if row is None:
            return None
        return _version_from_row(row)

    def published(self, *, team_id: str | None = None) -> tuple[PackageVersion, ...]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM package_versions WHERE status = 'published' AND (? IS NULL OR team_id = ?) ORDER BY package_id, version",
                (team_id, team_id),
            ).fetchall()
        return tuple(_version_from_row(row) for row in rows)

    def records(self, *, team_ids: tuple[str, ...] | None = None) -> tuple[PackageRecord, ...]:
        """索引用的包记录：只含至少有一个已发布版本的包，版本按时间升序。"""
        if team_ids is not None and not team_ids:
            return ()
        with self.database.transaction() as connection:
            if team_ids is None:
                package_rows = connection.execute(
                    "SELECT * FROM packages ORDER BY package_id"
                ).fetchall()
            else:
                placeholders = ", ".join("?" for _ in team_ids)
                package_rows = connection.execute(
                    f"SELECT * FROM packages WHERE team_id IN ({placeholders}) ORDER BY package_id",
                    tuple(team_ids),
                ).fetchall()
            version_rows = connection.execute(
                "SELECT * FROM package_versions WHERE status = 'published' "
                "ORDER BY package_id, created_at, version"
            ).fetchall()
        versions_by_package: dict[str, list[PackageVersion]] = {}
        for row in version_rows:
            versions_by_package.setdefault(str(row["package_id"]), []).append(_version_from_row(row))
        result: list[PackageRecord] = []
        for row in package_rows:
            package_id = str(row["package_id"])
            versions = versions_by_package.get(package_id)
            if not versions:
                continue
            result.append(PackageRecord(
                package_id, str(row["team_id"]), str(row["name"]),
                json.loads(row["metadata_json"] or "{}"), str(row["status"]),
                int(row["created_at"]), int(row["updated_at"]), tuple(versions),
            ))
        return tuple(result)

    def create_package(self, package_id: str, *, team_id: str, name: str, metadata: dict[str, object],
                       now: int | None = None) -> PackageRecord:
        """创建还没有版本的包实体。id 的第一段必须是所属 Team。"""
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if team_id == SYSTEM_TEAM_ID:
            raise ValueError("System workspace cannot publish Packages")
        if not _PACKAGE_ID.fullmatch(package_id):
            raise ValueError("package ID is invalid")
        if split_package_id(package_id)[0] != team_id:
            raise ValueError("package ID must start with its Team id")
        if not _ENTRY_NAME.fullmatch(name):
            raise ValueError("package name is invalid")
        validate_metadata(metadata)
        with self.database.transaction() as connection:
            try:
                connection.execute(
                    """INSERT INTO packages
                           (package_id, team_id, name, metadata_json, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'published', ?, ?)""",
                    (package_id, team_id, name, json.dumps(metadata, ensure_ascii=False), timestamp, timestamp),
                )
            except Exception as exc:
                raise ValueError(f"package already exists or is invalid: {exc}") from exc
        self._publish_change(
            kind="create",
            action="manage",
            package_id=package_id,
            team_id=team_id,
            data={"package_id": package_id, "name": name},
        )
        created = self.record(package_id, team_id=team_id)
        assert created is not None
        return created

    def record(self, package_id: str, *, team_id: str | None = None) -> PackageRecord | None:
        """包实体 + 它的全部版本（含未发布），用于管理端读取与版本历史。"""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM packages WHERE package_id = ? AND (? IS NULL OR team_id = ?)",
                (package_id, team_id, team_id),
            ).fetchone()
            if row is None:
                return None
            version_rows = connection.execute(
                "SELECT * FROM package_versions WHERE package_id = ? ORDER BY created_at, version",
                (package_id,),
            ).fetchall()
        return _package_record(row, version_rows)

    def update_package(self, package_id: str, *, team_id: str | None = None, name: str | None = None,
                       metadata: dict[str, object] | None = None, status: str | None = None,
                       now: int | None = None) -> PackageRecord:
        timestamp = int(time.time()) if now is None or now <= 0 else now
        if status is not None and status not in {"published", "disabled", "unpublished"}:
            raise ValueError("package status is invalid")
        if name is not None and not _ENTRY_NAME.fullmatch(name):
            raise ValueError("package name is invalid")
        if metadata is not None:
            validate_metadata(metadata)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM packages WHERE package_id = ? AND (? IS NULL OR team_id = ?)",
                (package_id, team_id, team_id),
            ).fetchone()
            if row is None:
                raise ValueError("package was not found")
            connection.execute(
                """UPDATE packages SET name = ?, metadata_json = ?, status = ?, updated_at = ?
                   WHERE package_id = ?""",
                (
                    str(row["name"]) if name is None else name,
                    json.dumps(dict(row["metadata_json"] and json.loads(row["metadata_json"]) or {})
                               if metadata is None else metadata, ensure_ascii=False),
                    str(row["status"]) if status is None else status,
                    timestamp,
                    package_id,
                ),
            )
        self._publish_change(
            kind="update",
            action="manage",
            package_id=package_id,
            team_id=team_id if team_id is not None else str(row["team_id"]),
            data={"package_id": package_id, "status": status},
        )
        updated = self.record(package_id)
        assert updated is not None
        return updated

    def search_records(self, *, team_id: str | None, package_id: str | None = None, limit: int = 50,
                       offset: int = 0) -> tuple[tuple[PackageRecord, ...], int]:
        """管理端包列表：一行一个包，附带全部版本（历史）。`team_id=None` 表示跨 Team（平台目录）。"""
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("package pagination is invalid")
        where = "" if team_id is None else "WHERE team_id = ?"
        values: list[object] = [] if team_id is None else [team_id]
        if package_id:
            where = f"{where} {'AND' if where else 'WHERE'} package_id = ?"
            values.append(package_id)
        with self.database.transaction() as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) AS total FROM packages {where}", tuple(values)
            ).fetchone()["total"])
            rows = connection.execute(
                f"SELECT * FROM packages {where} ORDER BY package_id LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            records: list[PackageRecord] = []
            for row in rows:
                version_rows = connection.execute(
                    "SELECT * FROM package_versions WHERE package_id = ? ORDER BY created_at, version",
                    (row["package_id"],),
                ).fetchall()
                records.append(_package_record(row, version_rows))
        return tuple(records), total

    def search(self, *, package_id: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0,
               team_id: str | None = None) -> \
            tuple[tuple[PackageVersion, ...], int]:
        if status is not None and status not in {"published", "disabled", "unpublished"}:
            raise ValueError("package status is invalid")
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("package pagination is invalid")
        clauses: list[str] = []
        values: list[object] = []
        if package_id:
            clauses.append("package_id = ?")
            values.append(package_id)
        if status:
            clauses.append("status = ?")
            values.append(status)
        if team_id:
            clauses.append("team_id = ?")
            values.append(team_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.database.transaction() as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) AS total FROM package_versions{where}", (*values,)
            ).fetchone()["total"])
            rows = connection.execute(
                f"SELECT * FROM package_versions{where} ORDER BY package_id, created_at DESC, version LIMIT ? OFFSET ?",
                (*values, limit, offset)).fetchall()
        return tuple(_version_from_row(row) for row in rows), total

    def set_status(self, package_id: str, version: str, status: str, *, team_id: str | None = None) -> None:
        if status not in {"published", "disabled", "unpublished"}:
            raise ValueError("package status is invalid")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE package_versions SET status = ? WHERE package_id = ? AND version = ? AND (? IS NULL OR team_id = ?)",
                (status, package_id, version, team_id, team_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("package version was not found")
        self._publish_change(
            kind="update",
            action="manage",
            package_id=package_id,
            team_id=team_id,
            data={"version": version, "status": status},
        )

    def update_metadata(self, package_id: str, version: str, metadata: dict[str, object], *,
                        team_id: str | None = None) -> PackageVersion:
        if not isinstance(metadata, dict):
            raise ValueError("package metadata must be an object")
        if any(key in metadata for key in IMMUTABLE_METADATA_FIELDS):
            raise ValueError("package metadata contains immutable fields")
        channel = metadata.get("channel")
        if channel is not None and (not isinstance(channel, str) or not _CHANNEL.fullmatch(channel)):
            raise ValueError("package channel is invalid")
        try:
            encoded = json.dumps(metadata, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("package metadata is not JSON serializable") from exc
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM package_versions WHERE package_id = ? AND version = ? AND (? IS NULL OR team_id = ?)",
                (package_id, version, team_id, team_id),
            ).fetchone()
            if row is None:
                raise ValueError("package version was not found")
            current = json.loads(row["metadata_json"] or "{}")
            if not isinstance(current, dict):
                current = {}
            current.update(json.loads(encoded))
            connection.execute(
                "UPDATE package_versions SET metadata_json = ? WHERE package_id = ? AND version = ? AND (? IS NULL OR team_id = ?)",
                (json.dumps(current, ensure_ascii=False), package_id, version, team_id, team_id),
            )
        updated = self.get(package_id, version, team_id=team_id)
        if updated is None:
            raise ValueError("package version was not found")
        self._publish_change(
            kind="update",
            action="manage",
            package_id=package_id,
            team_id=team_id,
            data={"version": version, "metadata": updated.metadata},
        )
        return updated

    def _publish_change(self, *, kind: str, action: str, package_id: str, team_id: str | None,
                        data: dict[str, object]) -> None:
        if self.events is None:
            return
        self.events.publish(ResourceChanged(
            kind=kind,
            node=package_resource_node(team_id or DEFAULT_TEAM_ID),
            action=action,
            data={"package_id": package_id, **data},
            team_id=team_id,
        ))
