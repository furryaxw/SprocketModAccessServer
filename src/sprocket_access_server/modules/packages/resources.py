from __future__ import annotations

from typing import Any

from ..resource_handlers import ok, refresh_permissions, require_field
from ...core.contracts import ModuleContext
from ...domain.errors import ApiError
from ...domain.resources import SYSTEM_TEAM_ID, package_resource_node
from ...infrastructure.events import ResourceChanged
from ...infrastructure.security.request_context import require_context_team, session_user
from ...modules.http_common import ApiResponse
from .store import IMMUTABLE_METADATA_FIELDS


def _service(context: ModuleContext, name: str) -> Any:
    return context.service(name)


def _request_key(headers: dict[str, str]) -> str:
    return next(
        (
            str(value).strip()
            for key, value in headers.items()
            if str(key).casefold() == "idempotency-key"
        ),
        "",
    )


def _run_idempotent(
        context: ModuleContext,
        *,
        scope: str,
        headers: dict[str, str],
        request: dict[str, object],
        operation,
        now: int | None,
) -> dict[str, object]:
    idempotency = _service(context, "idempotency")
    request_key = _request_key(headers)
    if idempotency is None or not request_key:
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
    return idempotency.run(
        scope=scope,
        request_key=request_key,
        request=request,
        operation=operation,
        now=now,
    )


def _download_url(context: ModuleContext, data: dict[str, Any], headers: dict[str, str],
                  now: int | None, *, team_id: str) -> Any:
    actor = session_user(_service(context, "authentication"), headers, now=now)
    package_id = require_field(data, "package_id")
    version = require_field(data, "version")
    package = _service(context, "packages").get(package_id, version, team_id=team_id)
    if package is None or package.status != "published":
        raise ApiError(404, "package_not_found", "published package version was not found")
    authorization = _service(context, "authorization_service")
    if not any(
            authorization.allows(actor, node, now=now, team_id=team_id)
            for node in (
                f"{package.permission_node}.download",
                f"team.{team_id}.packages.download",
            )):
        raise ApiError(403, "permission_denied", "package permission is not granted")
    codec = _service(context, "download_tokens")
    token = codec.issue(github_user_id=actor, package_id=package_id, version=version)
    return ok({
        "package_id": package_id,
        "version": version,
        "token": token,
        "expires_in": codec.max_ttl,
        "download_path": f"/v1/packages/{package_id}/download",
    })


def _create_upload(context: ModuleContext, data: dict[str, Any], headers: dict[str, str],
                   now: int | None, *, team_id: str) -> Any:
    if team_id == SYSTEM_TEAM_ID:
        raise ApiError(403, "permission_denied", "System workspace cannot publish Packages")
    actor = session_user(_service(context, "authentication"), headers, now=now)
    authorization = _service(context, "authorization_service")
    authorization.require_team(actor, team_id, "packages.manage", now=now)
    upload_store = _service(context, "upload_store")
    storage = _service(context, "direct_storage")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ApiError(400, "invalid_request", "metadata must be an object")
    package_id = require_field(data, "package_id").casefold()
    version = require_field(data, "version")
    size = int(data.get("size"))
    content_type = str(data.get("content_type", "application/octet-stream"))

    def operation() -> dict[str, object]:
        try:
            draft = upload_store.create(
                package_id=package_id,
                version=version,
                size=size,
                content_type=content_type,
                metadata=metadata,
                now=now,
                team_id=team_id,
            )
            timestamp = int(__import__("time").time()) if now is None or now <= 0 else now
            upload = storage.create_upload(
                object_key=draft.object_key,
                size=draft.size,
                content_type=draft.content_type,
                expires_in=draft.expires_at - timestamp,
            )
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        context.events.publish(ResourceChanged(
            kind="create",
            node=f"team.{team_id}.package_uploads",
            action="create",
            data={"upload_id": draft.upload_id, "package_id": draft.package_id, "version": draft.version},
            team_id=team_id,
            user_id=actor,
        ))
        return {
            "upload_id": draft.upload_id,
            "package_id": draft.package_id,
            "version": draft.version,
            "permission_node": draft.permission_node,
            "upload": upload,
            "expires_at": draft.expires_at,
            "created_by": actor,
        }

    result = _run_idempotent(
        context,
        scope=f"package-upload-create:{team_id}",
        headers=headers,
        request={
            "package_id": package_id,
            "version": version,
            "size": size,
            "content_type": content_type,
            "metadata": metadata,
        },
        operation=operation,
        now=now,
    )
    return ApiResponse(201, result)


def _record_payload(record: Any) -> dict[str, object]:
    """管理端的包形状：一行一个包，版本作为历史挂在下面。"""
    return {
        "package_id": record.package_id,
        "team_id": record.team_id,
        "name": record.name,
        "status": record.status,
        "metadata": dict(record.metadata),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "version_count": len(record.versions),
        "versions": [
            {
                "version": item.version,
                "status": item.status,
                "archive_size": item.archive_size,
                "archive_digest": item.archive_digest,
                "created_at": item.created_at,
            }
            for item in record.versions
        ],
    }


def _publisher_base(context: ModuleContext) -> str:
    """发布器上配置的服务端公开 origin，用于响应的下载端点绝对 URL。"""
    publisher = context.services.get("publisher")
    return str(getattr(publisher, "download_base_url", "") or "")


def _confirm_upload(context: ModuleContext, data: dict[str, Any], headers: dict[str, str],
                    now: int | None, *, team_id: str) -> Any:
    if team_id == SYSTEM_TEAM_ID:
        raise ApiError(403, "permission_denied", "System workspace cannot publish Packages")
    actor = session_user(_service(context, "authentication"), headers, now=now)
    authorization = _service(context, "authorization_service")
    authorization.require_team(actor, team_id, "packages.manage", now=now)
    upload_store = _service(context, "upload_store")
    storage = _service(context, "direct_storage")
    publisher = _service(context, "publisher")
    upload_id = require_field(data, "upload_id")
    digest = require_field(data, "sha256")
    if len(digest) != 64:
        raise ApiError(400, "invalid_request", "sha256 digest is required")

    def operation() -> dict[str, object]:
        try:
            draft = upload_store.get_pending(upload_id, now=now, team_id=team_id)
            storage.confirm_upload(object_key=draft.object_key, digest=digest, size=draft.size)
            # 元数据属于包：版本记录快照包实体的元数据（首次上传会自动建立包实体）。
            entity = _service(context, "packages").record(draft.package_id, team_id=team_id)
            metadata = dict(entity.metadata) if entity is not None else draft.metadata
            result = publisher.register_existing(
                package_id=draft.package_id,
                version=draft.version,
                digest=digest,
                size=draft.size,
                metadata=metadata,
                now=now,
                team_id=team_id,
            )
            upload_store.mark_confirmed(upload_id, now=now, team_id=team_id)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        audit = context.services.get("audit")
        if audit is not None:
            audit.record(
                actor=f"github:{actor}",
                action="package.publish",
                target=f"{draft.package_id}@{draft.version}",
                metadata={"archive_size": draft.size, "archive_digest": digest, "upload_id": upload_id},
                now=int(__import__("time").time()) if now is None or now <= 0 else now,
                team_id=team_id,
            )
        context.events.publish(ResourceChanged(
            kind="update",
            node=f"team.{team_id}.package_uploads",
            action="confirm",
            data={"upload_id": upload_id, "package_id": draft.package_id, "version": draft.version},
            team_id=team_id,
            user_id=actor,
        ))
        return result.manifest

    result = _run_idempotent(
        context,
        scope=f"package-upload-confirm:{team_id}",
        headers=headers,
        request={"upload_id": upload_id, "sha256": digest},
        operation=operation,
        now=now,
    )
    return ApiResponse(201, result)


def _package_update_preview(current: Any, metadata: dict[str, object], status: object | None) -> dict[str, object]:
    immutable = sorted(key for key in metadata if key in IMMUTABLE_METADATA_FIELDS)
    current_metadata = dict(current.metadata)
    next_status = current.status if status is None else str(status)
    added = sorted(key for key in metadata if key not in current_metadata)
    changed = sorted(
        key for key, value in metadata.items()
        if key in current_metadata and current_metadata[key] != value
    )
    return {
        "package_id": current.package_id,
        "version": current.version,
        "current": {"status": current.status, "metadata": current_metadata},
        "updated": {"status": next_status, "metadata": {**current_metadata, **metadata}},
        "diff": {
            "metadata_added": added,
            "metadata_changed": changed,
            "status_changed": next_status != current.status,
            "immutable_fields": immutable,
        },
        "requires_confirmation": next_status != current.status,
    }


def attach(context: ModuleContext) -> None:
    resources = context.resources
    platform = context.services.get("platform")
    if platform is None:
        return

    def system_read(data: dict[str, Any], headers: dict[str, str], now: int | None):
        require_context_team(headers, SYSTEM_TEAM_ID)
        actor = session_user(_service(context, "authentication"), headers, now=now)
        authorization = _service(context, "authorization_service")
        authorization.require_team(actor, SYSTEM_TEAM_ID, "packages.read", now=now)
        store = _service(context, "packages")
        try:
            limit = int(data.get("limit", 50))
            offset = int(data.get("offset", 0))
            # 平台目录跨 Team：与 Team 节点同形状（一行一个包），另带 team_id 说明归属。
            records, total = store.search_records(limit=limit, offset=offset, team_id=None)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        return ok({
            "packages": [_record_payload(item) for item in records],
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    resources.register("team.system.packages", description="System package catalog").add_perm("read", system_read)

    def register_package_resources(team_id: str) -> None:
        if team_id == SYSTEM_TEAM_ID:
            return

        def create(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            """创建包实体：id 由 `<team>.<mod>` 构造，版本稍后单独上传。"""
            actor = session_user(_service(context, "authentication"), headers, now=now)
            authorization = _service(context, "authorization_service")
            authorization.require_team(actor, team_id, "packages.manage", now=now)
            mod = str(data.get("mod", "")).strip().casefold()
            if not mod:
                raise ApiError(400, "invalid_request", "mod is required")
            metadata = data.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ApiError(400, "invalid_request", "metadata must be an object")
            name = str(metadata.get("name", "")).strip() or mod
            try:
                record = _service(context, "packages").create_package(
                    f"{team_id}.{mod}", team_id=team_id, name=name, metadata=dict(metadata), now=now,
                )
            except ValueError as exc:
                raise ApiError(400, "invalid_request", str(exc)) from exc
            context.events.publish(ResourceChanged(
                kind="create",
                node=f"team.{team_id}.packages",
                action="create",
                data={"package_id": record.package_id},
                team_id=team_id,
                user_id=actor,
            ))
            return ApiResponse(201, _record_payload(record))

        def read(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            actor = session_user(_service(context, "authentication"), headers, now=now)
            authorization = _service(context, "authorization_service")
            authorization.require_team(actor, team_id, "packages.read", now=now)
            store = _service(context, "packages")
            try:
                limit = int(data.get("limit", 50))
                offset = int(data.get("offset", 0))
                package_id = str(data.get("package_id", "")).strip() or None
                records, total = store.search_records(
                    team_id=team_id, package_id=package_id, limit=limit, offset=offset,
                )
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "invalid_request", str(exc)) from exc
            return ok({
                "packages": [_record_payload(item) for item in records],
                "total": total,
                "limit": limit,
                "offset": offset,
            })

        def manage(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            actor = session_user(_service(context, "authentication"), headers, now=now)
            authorization = _service(context, "authorization_service")
            authorization.require_team(actor, team_id, "packages.manage", now=now)
            package_id = require_field(data, "package_id")
            status = data.get("status")
            if status is not None and status not in {"published", "disabled", "unpublished"}:
                raise ApiError(400, "invalid_request", "package status is invalid")
            metadata = data.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ApiError(400, "invalid_request", "metadata must be an object")
            if not str(data.get("version", "")).strip():
                # 包级编辑：元数据与可见性属于包；没有 version 就走这条。
                try:
                    record = _service(context, "packages").update_package(
                        package_id,
                        team_id=team_id,
                        name=str(data["name"]) if "name" in data else None,
                        metadata=metadata if "metadata" in data else None,
                        status=status,
                        now=now,
                    )
                except ValueError as exc:
                    raise ApiError(400, "invalid_request", str(exc)) from exc
                context.events.publish(ResourceChanged(
                    kind="update",
                    node=f"team.{team_id}.packages",
                    action="manage",
                    data={"package_id": package_id},
                    team_id=team_id,
                    user_id=actor,
                ))
                return ok(_record_payload(record))
            version = require_field(data, "version")
            current = _service(context, "packages").get(package_id, version, team_id=team_id)
            if current is None:
                raise ApiError(404, "package_not_found", "package version was not found")
            preview = _package_update_preview(current, metadata, status)
            if data.get("preview") is True:
                return ok(preview)
            idempotency = _service(context, "idempotency")
            request_key = next(
                (
                    str(value).strip()
                    for key, value in headers.items()
                    if str(key).casefold() == "idempotency-key"
                ),
                "",
            )
            if idempotency is None or not request_key:
                raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

            def operation() -> dict[str, object]:
                if status is not None:
                    confirmation_token = str(data.get("confirmation_token", "")).strip()
                    try:
                        _service(context, "confirmations").consume(
                            confirmation_token,
                            action="package.confirm",
                            target=f"{package_id}:{version}",
                            now=now,
                        )
                    except ValueError as exc:
                        raise ApiError(400, "confirmation_required", str(exc)) from exc
                    _service(context, "packages").set_status(
                        package_id, version, status, team_id=team_id,
                    )
                updated = _service(context, "packages").update_metadata(
                    package_id, version, metadata, team_id=team_id
                )
                return updated.manifest(download_base_url=_publisher_base(context))

            return ok(idempotency.run(
                scope=f"package-metadata:{team_id}",
                request_key=request_key,
                request={
                    "package_id": package_id,
                    "version": version,
                    "metadata": metadata,
                    "status": status,
                },
                operation=operation,
                now=now,
            ))

        def confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None, *, team_id: str = team_id):
            actor = session_user(_service(context, "authentication"), headers, now=now)
            _service(context, "authorization_service").require_team(actor, team_id, "packages.manage", now=now)
            package_id = require_field(data, "package_id")
            version = require_field(data, "version")
            token, confirmation_obj = _service(context, "confirmations").issue(
                action="package.confirm",
                target=f"{package_id}:{version}",
                created_by=actor,
                now=now,
            )
            return ok({"confirmation_token": token, "confirmation": confirmation_obj.__dict__})

        packages_node = f"team.{team_id}.packages"
        if resources.resolve("read", packages_node) is None:
            resources.register(packages_node, description="Team packages") \
                .add_perm("read", read) \
                .add_perm("create", create) \
                .add_perm("manage", manage) \
                .add_perm("confirm", confirmation) \
                .add_perm("download", lambda data, headers, now, *, team_id=team_id:
                          _download_url(context, data, headers, now, team_id=team_id))
        uploads_node = f"team.{team_id}.package_uploads"
        if resources.resolve("create", uploads_node) is None:
            resources.register(uploads_node, description="Team package uploads") \
                .add_perm("create", lambda data, headers, now, *, team_id=team_id:
                          _create_upload(context, data, headers, now, team_id=team_id)) \
                .add_perm("confirm", lambda data, headers, now, *, team_id=team_id:
                          _confirm_upload(context, data, headers, now, team_id=team_id))

        def on_package_created(event: ResourceChanged, *, team_id: str = team_id) -> None:
            if event.kind != "create" or event.team_id != team_id:
                return
            package_id = str(event.data.get("package_id", "")).strip()
            if not package_id:
                return
            node = package_resource_node(team_id, package_id)
            if resources.resolve("read", node) is None:
                resources.register(node, description=f"Package {package_id}", auto_grant=True) \
                    .add_perm("read", read) \
                    .add_perm("manage", manage) \
                    .add_perm("confirm", confirmation) \
                    .add_perm("download", lambda data, headers, now, *, team_id=team_id:
                              _download_url(context, data, headers, now, team_id=team_id))
                refresh_permissions(context)

        context.events.subscribe(ResourceChanged, on_package_created)

    for row in platform.teams():
        register_package_resources(str(row["team_id"]))

    def on_team_created(event: ResourceChanged) -> None:
        if event.kind != "create" or not event.node.startswith("team."):
            return
        team_id = str(event.team_id or event.node.removeprefix("team.")).strip()
        if not team_id or "." in team_id:
            return
        with resources.module_scope():
            register_package_resources(team_id)
        refresh_permissions(context)

    context.events.subscribe(ResourceChanged, on_team_created)
