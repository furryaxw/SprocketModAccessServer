from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from sprocket_access_server.domain.errors import ApiError
from sprocket_access_server.modules.http_common import ApiResponse
from sprocket_access_server.modules.resource_handlers import refresh_permissions


def register_static_test_resources(api) -> None:
    if api.module_context is None:
        return

    def entitlements_read(data, headers, now):
        scheme, _, token = api._header(headers, "Authorization").partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            raise ApiError(401, "invalid_session", "session is invalid or expired")
        user_id = api.authentication.authenticate_session(token.strip(), now=now)
        permissions = api.authorization.permissions(user_id, now=now) if api.authorization else frozenset()
        grants = [{"grant_id": grant.grant_id, "github_user_id": grant.github_user_id,
                   "permissions": sorted(grant.permissions), "status": grant.status.value,
                   "expires_at": grant.expires_at}
                  for grant in api.authorization.grants(user_id)] if api.authorization else []
        return ApiResponse(200, {"github_user_id": user_id, "permissions": sorted(permissions), "grants": grants})

    api.resources.register("system.entitlements", description="Current entitlements").add_perm("read", entitlements_read)
    refresh_permissions(api.module_context)


def register_team_runtime_resources(api, team_id: str) -> None:
    resources = api.resources

    def package_read(data: dict[str, Any], headers: dict[str, str], now: int | None):
        path = "/admin/packages"
        if data:
            path = f"{path}?{urlencode({key: value for key, value in data.items() if value is not None})}"
        return api._search_admin_packages(path, headers, now=now)

    def package_download_url(data: dict[str, Any], headers: dict[str, str], now: int | None):
        package_id = str(data.get("package_id", "")).strip()
        version = str(data.get("version", "")).strip()
        if not package_id:
            raise ApiError(400, "invalid_request", "package_id is required")
        if not version:
            raise ApiError(400, "invalid_request", "version is required")
        payload = {key: value for key, value in data.items() if key != "package_id"}
        return api._issue_download_url(package_id, json.dumps(payload).encode("utf-8"), headers, now=now)

    def package_confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None):
        package_id = str(data.get("package_id", "")).strip()
        version = str(data.get("version", "")).strip()
        if not package_id:
            raise ApiError(400, "invalid_request", "package_id is required")
        if not version:
            raise ApiError(400, "invalid_request", "version is required")
        return api._issue_package_confirmation(f"/admin/packages/{package_id}/{version}/confirmation", headers, now=now)

    def package_status(data: dict[str, Any], headers: dict[str, str], now: int | None):
        package_id = str(data.get("package_id", "")).strip()
        version = str(data.get("version", "")).strip()
        if not package_id:
            raise ApiError(400, "invalid_request", "package_id is required")
        if not version:
            raise ApiError(400, "invalid_request", "version is required")
        body = json.dumps({key: value for key, value in data.items() if key != "package_id"}).encode("utf-8")
        return api._change_package_status(f"/admin/packages/{package_id}/{version}/status", body, headers, now=now)

    def package_metadata(data: dict[str, Any], headers: dict[str, str], now: int | None):
        package_id = str(data.get("package_id", "")).strip()
        version = str(data.get("version", "")).strip()
        if not package_id:
            raise ApiError(400, "invalid_request", "package_id is required")
        if not version:
            raise ApiError(400, "invalid_request", "version is required")
        body = json.dumps({key: value for key, value in data.items() if key != "package_id"}).encode("utf-8")
        return api._update_package_metadata(f"/admin/packages/{package_id}/{version}/metadata", body, headers, now=now)

    def package_upload_create(data: dict[str, Any], headers: dict[str, str], now: int | None):
        return api._create_upload(json.dumps(data).encode("utf-8"), headers, now=now)

    def package_upload_confirm(data: dict[str, Any], headers: dict[str, str], now: int | None):
        upload_id = str(data.get("upload_id", "")).strip()
        if not upload_id:
            raise ApiError(400, "invalid_request", "upload_id is required")
        body = json.dumps({key: value for key, value in data.items() if key != "upload_id"}).encode("utf-8")
        return api._confirm_upload(upload_id, body, headers, now=now)

    def audit_read(data: dict[str, Any], headers: dict[str, str], now: int | None):
        path = "/admin/audit"
        if data:
            path = f"{path}?{urlencode({key: value for key, value in data.items() if value is not None})}"
        return api._search_audit(path, headers, now=now)

    def audit_export(data: dict[str, Any], headers: dict[str, str], now: int | None):
        path = "/admin/audit/export"
        if data:
            path = f"{path}?{urlencode({key: value for key, value in data.items() if value is not None})}"
        return api._export_audit(path, headers, now=now)

    def template_read(data: dict[str, Any], headers: dict[str, str], now: int | None):
        return api._list_permission_templates(headers, now=now)

    def template_create(data: dict[str, Any], headers: dict[str, str], now: int | None):
        return api._create_permission_template(json.dumps(data).encode("utf-8"), headers, now=now)

    def template_confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None):
        template_id = str(data.get("template_id", "")).strip()
        if not template_id:
            raise ApiError(400, "invalid_request", "template_id is required")
        return api._issue_template_confirmation(f"/admin/permission-templates/{template_id}/confirmation",
                                                headers, now=now)

    def template_manage(data: dict[str, Any], headers: dict[str, str], now: int | None):
        template_id = str(data.get("template_id", "")).strip()
        if not template_id:
            raise ApiError(400, "invalid_request", "template_id is required")
        body = json.dumps({key: value for key, value in data.items() if key != "template_id"}).encode("utf-8")
        return api._update_permission_template(f"/admin/permission-templates/{template_id}", body, headers, now=now)

    def assignment_read(data: dict[str, Any], headers: dict[str, str], now: int | None):
        path = "/admin/permission-assignments"
        if data:
            path = f"{path}?{urlencode({key: value for key, value in data.items() if value is not None})}"
        return api._search_grants(path, headers, now=now)

    def assignment_confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None):
        grant_id = str(data.get("assignment_id", "")).strip() or str(data.get("grant_id", "")).strip()
        if not grant_id:
            raise ApiError(400, "invalid_request", "assignment_id is required")
        suffix = "/suspend/confirmation" if str(data.get("mode", "")).strip().casefold() == "suspend" else "/confirmation"
        return api._issue_grant_confirmation(f"/admin/permission-assignments/{grant_id}{suffix}",
                                             headers, now=now)

    def assignment_edit_confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None):
        grant_id = str(data.get("assignment_id", "")).strip() or str(data.get("grant_id", "")).strip()
        if not grant_id:
            raise ApiError(400, "invalid_request", "assignment_id is required")
        return api._issue_grant_edit_confirmation(
            f"/admin/permission-assignments/{grant_id}/edit-confirmation",
            headers,
            now=now,
        )

    def assignment_manage(data: dict[str, Any], headers: dict[str, str], now: int | None):
        grant_id = str(data.get("assignment_id", "")).strip() or str(data.get("grant_id", "")).strip()
        if not grant_id:
            raise ApiError(400, "invalid_request", "assignment_id is required")
        body = json.dumps({key: value for key, value in data.items() if key not in {"assignment_id", "grant_id"}}).encode("utf-8")
        return api._update_grant(f"/admin/permission-assignments/{grant_id}", body, headers, now=now)

    def assignment_revoke(data: dict[str, Any], headers: dict[str, str], now: int | None):
        grant_id = str(data.get("assignment_id", "")).strip() or str(data.get("grant_id", "")).strip()
        if not grant_id:
            raise ApiError(400, "invalid_request", "assignment_id is required")
        return api._revoke_grant(f"/admin/permission-assignments/{grant_id}/revoke", headers, now=now)

    def assignment_suspend(data: dict[str, Any], headers: dict[str, str], now: int | None):
        grant_id = str(data.get("assignment_id", "")).strip() or str(data.get("grant_id", "")).strip()
        if not grant_id:
            raise ApiError(400, "invalid_request", "assignment_id is required")
        return api._change_grant_status(f"/admin/permission-assignments/{grant_id}/suspend", "suspended",
                                        headers, now=now)

    def assignment_extend(data: dict[str, Any], headers: dict[str, str], now: int | None):
        grant_id = str(data.get("assignment_id", "")).strip() or str(data.get("grant_id", "")).strip()
        if not grant_id:
            raise ApiError(400, "invalid_request", "assignment_id is required")
        body = json.dumps({key: value for key, value in data.items() if key not in {"assignment_id", "grant_id"}}).encode("utf-8")
        return api._extend_grant(f"/admin/permission-assignments/{grant_id}/extend", body, headers, now=now)

    def team_keys_read(data: dict[str, Any], headers: dict[str, str], now: int | None):
        path = "/admin/keys"
        if data:
            path = f"{path}?{urlencode({key: value for key, value in data.items() if value is not None})}"
        return api._search_keys(path, headers, now=now)

    def team_key_batch_create(data: dict[str, Any], headers: dict[str, str], now: int | None):
        return api._create_key_batch(json.dumps(data).encode("utf-8"), headers, now=now)

    def team_key_batch_stats(data: dict[str, Any], headers: dict[str, str], now: int | None):
        batch_id = str(data.get("batch_id", "")).strip()
        if not batch_id:
            raise ApiError(400, "invalid_request", "batch_id is required")
        return api._key_batch_stats(f"/admin/key-batches/{batch_id}/stats", headers, now=now)

    def team_key_confirmation(data: dict[str, Any], headers: dict[str, str], now: int | None):
        key_id = str(data.get("key_id", "")).strip()
        if not key_id:
            raise ApiError(400, "invalid_request", "key_id is required")
        return api._issue_key_confirmation(f"/admin/keys/{key_id}/confirmation", headers, now=now)

    def team_key_manage(data: dict[str, Any], headers: dict[str, str], now: int | None):
        key_id = str(data.get("key_id", "")).strip()
        if not key_id:
            raise ApiError(400, "invalid_request", "key_id is required")
        body = json.dumps({key: value for key, value in data.items() if key != "key_id"}).encode("utf-8")
        return api._update_key(f"/admin/keys/{key_id}", body, headers, now=now)

    def team_package_catalog(data: dict[str, Any], headers: dict[str, str], now: int | None):
        if api.authorization is None or api.packages is None:
            raise ApiError(503, "distribution_unavailable", "package distribution is not configured")
        user_id, _ = api._session_user(headers, now=now)
        selected_team_id = api._team_id(headers, user_id, required=api.platform is not None) or team_id
        items = []
        for package in api.packages.published(team_id=selected_team_id):
            visible = any(
                api.authorization.allows(user_id, node, now=now, team_id=selected_team_id)
                for node in (
                    f"{package.permission_node}.read",
                    f"{package.permission_node}.manage",
                    f"{package.permission_node}.download",
                    f"team.{selected_team_id}.packages.read",
                    f"team.{selected_team_id}.packages.manage",
                    f"team.{selected_team_id}.packages.download",
                )
            )
            if not visible:
                continue
            manifest = package.manifest()
            if api.signing_key is not None and api.signing_key_id:
                manifest = package.signed_manifest(api.signing_key, api.signing_key_id)
            items.append(manifest)
        return ApiResponse(200, {"packages": items})

    def team_package_read(data: dict[str, Any], headers: dict[str, str], now: int | None):
        path = "/admin/packages"
        if data:
            path = f"{path}?{urlencode({key: value for key, value in data.items() if value is not None})}"
        return api._search_admin_packages(path, headers, now=now)

    # 测试适配器只补真实模块没有注册的动作：Team 通过事件创建时模块已经注册过同名节点，
    # 无条件重注册会以 duplicate resource operation 结束。
    def add(node: str, description: str, action: str, handler) -> None:
        if resources.resolve(action, node) is not None:
            return
        resources.register(node, description=description).add_perm(action, handler)

    keys_node = f"team.{team_id}.keys"
    add(keys_node, "Team keys", "read", team_keys_read)
    add(keys_node, "Team keys", "create", team_key_batch_create)
    add(keys_node, "Team keys", "read_batch", team_key_batch_stats)
    add(keys_node, "Team keys", "confirm", team_key_confirmation)
    add(keys_node, "Team keys", "manage", team_key_manage)
    add(keys_node, "Team keys", "distribute", team_key_batch_create)
    packages_node = f"team.{team_id}.packages"
    add(packages_node, "Team Packages", "read", team_package_read)
    add(packages_node, "Team Packages", "download", package_download_url)
    add(packages_node, "Team Packages", "confirm", package_confirmation)
    add(packages_node, "Team Packages", "status", package_status)
    add(packages_node, "Team Packages", "metadata", package_metadata)
    add(f"team.{team_id}.packages.catalog", "Team package catalog", "read", team_package_catalog)
    uploads_node = f"team.{team_id}.package_uploads"
    add(uploads_node, "Team package uploads", "create", package_upload_create)
    add(uploads_node, "Team package uploads", "confirm", package_upload_confirm)
    templates_node = f"team.{team_id}.permission_templates"
    add(templates_node, "Team permission templates", "read", template_read)
    add(templates_node, "Team permission templates", "create", template_create)
    add(templates_node, "Team permission templates", "confirm", template_confirmation)
    add(templates_node, "Team permission templates", "manage", template_manage)
    assignments_node = f"team.{team_id}.permission_assignments"
    add(assignments_node, "Team permission assignments", "read", assignment_read)
    add(assignments_node, "Team permission assignments", "confirm", assignment_confirmation)
    add(assignments_node, "Team permission assignments", "edit_confirmation", assignment_edit_confirmation)
    add(assignments_node, "Team permission assignments", "manage", assignment_manage)
    add(assignments_node, "Team permission assignments", "revoke", assignment_revoke)
    add(assignments_node, "Team permission assignments", "suspend", assignment_suspend)
    add(assignments_node, "Team permission assignments", "extend", assignment_extend)
    audit_node = f"team.{team_id}.audit"
    add(audit_node, "Team audit log", "read", audit_read)
    add(audit_node, "Team audit log", "export", audit_export)
