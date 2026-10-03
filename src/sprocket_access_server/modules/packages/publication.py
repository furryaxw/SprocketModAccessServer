from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass
from typing import BinaryIO, Any

from ...core.ports import ObjectStorage
from ...domain.resources import SYSTEM_TEAM_ID
from ...modules.packages.files import payload_kind, validate_payload
from ...modules.packages.store import PackageVersion, SQLitePackageStore


@dataclass(frozen=True)
class PublishedPackage:
    package: PackageVersion
    manifest: dict[str, object]


class PackagePublisher:
    def __init__(self, packages: SQLitePackageStore, storage: ObjectStorage,
                 *, download_base_url: str = "") -> None:
        self.packages = packages
        self.storage = storage
        # 服务端自己的公开 origin：发布响应用它拼下载端点的绝对 URL。
        self.download_base_url = download_base_url.strip().rstrip("/")

    def publish_archive(
            self,
            *,
            package_id: str,
            version: str,
            archive: bytes,
            metadata: dict[str, Any] | None = None,
            team_id: str | None = None,
            now: int | None = None,
    ) -> PublishedPackage:
        if team_id == SYSTEM_TEAM_ID:
            raise ValueError("System workspace cannot publish Packages")
        return self.publish_stream(
            package_id=package_id,
            version=version,
            source=io.BytesIO(archive),
            size=len(archive),
            metadata=metadata,
            now=now,
            team_id=team_id,
        )

    def publish_stream(
            self,
            *,
            package_id: str,
            version: str,
            source: BinaryIO,
            size: int,
            metadata: dict[str, Any] | None = None,
            team_id: str | None = None,
            now: int | None = None,
    ) -> PublishedPackage:
        if team_id == SYSTEM_TEAM_ID:
            raise ValueError("System workspace cannot publish Packages")
        if size < 1:
            raise ValueError("package archive is empty")
        hasher = hashlib.sha256()
        total = 0
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > size:
                raise ValueError("package archive exceeds declared size")
            hasher.update(chunk)
        if total != size:
            raise ValueError("package archive size does not match declaration")
        digest = hasher.hexdigest()
        source.seek(0)
        kind = payload_kind(source)
        snapshot = self._snapshot_metadata(package_id, metadata, team_id)
        # 载荷即契约：包声明的安装规则决定去向，规则缺失或覆盖不到条目即拒绝发布。
        validate_payload(source, install=_install_rules(snapshot))
        source.seek(0)
        self.storage.put(source, digest=digest, size=size)
        package = PackageVersion(
            package_id, version, "", digest, size, "published",
            int(time.time()) if now is None or now <= 0 else now,
            snapshot,
            team_id,
            kind,
        )
        try:
            self.packages.publish(package, now=now, team_id=team_id)
        except Exception:
            # Content-addressed bytes are harmless without a package row and can
            # be garbage-collected by a later storage sweep.
            raise
        return PublishedPackage(package, self._entity_entry(package, team_id=team_id))

    def register_existing(
            self,
            *,
            package_id: str,
            version: str,
            digest: str,
            size: int,
            metadata: dict[str, Any] | None = None,
            team_id: str | None = None,
            now: int | None = None,
    ) -> PublishedPackage:
        if team_id == SYSTEM_TEAM_ID:
            raise ValueError("System workspace cannot publish Packages")
        if not self.storage.exists(digest) or size < 1:
            raise ValueError("confirmed package object is unavailable")
        # 已上传的字节在存储里；发布前同样按包声明的规则校验载荷。
        snapshot = self._snapshot_metadata(package_id, metadata, team_id)
        with self.storage.open(digest) as stored:
            kind = payload_kind(stored)
            validate_payload(stored, install=_install_rules(snapshot))
        package = PackageVersion(
            package_id, version, "", digest, size, "published",
            int(time.time()) if now is None or now <= 0 else now,
            snapshot,
            team_id,
            kind,
        )
        self.packages.publish(package, now=now, team_id=team_id)
        return PublishedPackage(package, self._entity_entry(package, team_id=team_id))

    def _snapshot_metadata(self, package_id: str, metadata: dict[str, Any] | None,
                           team_id: str | None) -> dict[str, Any]:
        """版本快照记录的元数据。

        元数据与安装规则都属于包：包实体已存在时以它为准（管理员改过规则后，新版本的发布按新规则
        校验与落库）；实体尚未建立（首次发布）时用本次传入的 metadata。
        """
        record = self.packages.record(package_id, team_id=team_id)
        if record is not None:
            return dict(record.metadata)
        return {} if metadata is None else dict(metadata)

    def _entity_entry(self, package: PackageVersion, *, team_id: str | None) -> dict[str, object]:
        """发布响应用**包实体**的条目。

        安装规则属于包而不是版本：用版本快照会让响应在包元数据更新后继续显示旧规则。
        """
        record = self.packages.record(package.package_id, team_id=team_id)
        if record is not None:
            return record.entry(download_base_url=self.download_base_url)
        return package.manifest(download_base_url=self.download_base_url)


def _install_rules(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    install = (metadata or {}).get("install")
    return install if isinstance(install, dict) else None
