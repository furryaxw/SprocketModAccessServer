from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from sprocket_access_server.domain.errors import ApiError
from sprocket_access_server.modules.http_common import ApiResponse


class HarnessKeyOperationsMixin:
    """Key operation adapter contract for the test resource harness."""

    def key_search(self, path, headers, *, now=None):
        return self._search_keys(path, headers, now=now)

    def key_batch_create(self, body, headers, *, now=None):
        return self._create_key_batch(body, headers, now=now)

    def key_batch_stats(self, path, headers, *, now=None):
        return self._key_batch_stats(path, headers, now=now)

    def key_confirmation(self, path, headers, *, now=None):
        return self._issue_key_confirmation(path, headers, now=now)

    def key_update(self, path, body, headers, *, now=None):
        return self._update_key(path, body, headers, now=now)

    def _search_keys(self, path, headers, *, now=None):
        if self.key_issuer is None:
            raise ApiError(503, "admin_unavailable", "key service is not configured")
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.keys.distribute")
        team_id = self._team_id(headers, actor)
        query = parse_qs(urlsplit(path).query)
        status = query.get("status", [None])[0]
        prefix = query.get("prefix", [""])[0]
        try:
            limit = int(query.get("limit", ["50"])[0])
            offset = int(query.get("offset", ["0"])[0])
            rows, counts, total = self.key_issuer.search(status=status, prefix=prefix, limit=limit, offset=offset, team_id=team_id)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        return ApiResponse(200, {"keys": [row.__dict__ for row in rows], "counts": counts, "total": total, "limit": limit, "offset": offset})

    def _key_batch_stats(self, path, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.keys.distribute")
        if self.key_issuer is None:
            raise ApiError(503, "admin_unavailable", "key service is not configured")
        batch_id = path[len("/admin/key-batches/"):-len("/stats")].strip("/")
        try:
            stats = self.key_issuer.batch_stats(batch_id, team_id=self._team_id(headers, actor))
        except ValueError as exc:
            code = "key_batch_not_found" if "not found" in str(exc) else "invalid_request"
            raise ApiError(404 if code.endswith("not_found") else 400, code, str(exc)) from exc
        return ApiResponse(200, stats)

    def _issue_key_confirmation(self, path, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.keys.distribute")
        if self.key_issuer is None or self.confirmations is None:
            raise ApiError(503, "admin_unavailable", "key management is not configured")
        key_id = path[len("/admin/keys/"):-len("/confirmation")].strip("/")
        if not key_id:
            raise ApiError(400, "invalid_request", "key_id is required")
        try:
            self.key_issuer.preview_update(key_id, team_id=self._team_id(headers, actor))
        except ValueError as exc:
            raise ApiError(404, "key_not_found", str(exc)) from exc
        token, confirmation = self.confirmations.issue(action="key.update", target=key_id, created_by=actor, now=now)
        return ApiResponse(200, {"confirmation_token": token, "action": confirmation.action,
                                 "target": confirmation.target, "expires_at": confirmation.expires_at},
                           {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def _create_key_batch(self, body, headers, *, now=None):
        if self.key_issuer is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "administrator service is not configured")
        payload = self._json_body(body)
        scheme, _, token = self._header(headers, "Authorization").partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            raise ApiError(401, "invalid_session", "session is invalid or expired")
        user_id = self.authentication.authenticate_session(token.strip(), now=now)
        team_id = self._team_id(headers, user_id, required=self.platform is not None)
        if self.authorization_service is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        self.authorization_service.require_team(user_id, team_id or "default", "keys.distribute", now=now)
        request_key = self._header(headers, "Idempotency-Key").strip()
        request = {"quantity": payload.get("quantity"), "permissions": payload.get("permissions"),
                   "expires_at": payload.get("expires_at"), "template_id": payload.get("template_id")}
        def operation() -> dict[str, object]:
            template_id = payload.get("template_id")
            if template_id is not None:
                if not isinstance(template_id, str) or not template_id.strip() or self.permission_templates is None:
                    raise ApiError(400, "invalid_request", "template_id is invalid")
                template = self.permission_templates.get(template_id.strip(), team_id=team_id)
                if template is None:
                    raise ApiError(404, "template_not_found", "grant template was not found")
                if payload.get("permissions") is not None:
                    raise ApiError(400, "invalid_request", "template_id and permissions are mutually exclusive")
                permissions = sorted(template.permissions)
            else:
                permissions = payload.get("permissions")
                if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions):
                    raise ApiError(400, "invalid_request", "permissions must be a string array")
            if self.permission_catalog is not None:
                try:
                    permissions = sorted(self.permission_catalog.validate(permissions))
                except ValueError as exc:
                    raise ApiError(400, "invalid_request", str(exc)) from exc
            self.authorization_service.require_grantable(user_id, permissions, team_id=team_id, now=now)
            try:
                quantity = int(payload.get("quantity")); expires_at = payload.get("expires_at")
                if expires_at is not None: expires_at = int(expires_at)
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "invalid_request", "quantity and expires_at must be integers") from exc
            if template_id is not None and expires_at is None:
                expires_at = (int(__import__("time").time()) if now is None else now) + template.expires_in if template.expires_in is not None else None
            keys = self.key_issuer.create_batch(quantity=quantity, permissions=frozenset(permissions), expires_at=expires_at,
                actor=user_id, team_id=team_id, assignments=template.assignments if template_id is not None else None,
                template_id=template_id, key_kind="template" if template_id is not None else "snapshot", now=now)
            if self.audit is not None:
                self.audit.record(actor=f"github:{user_id}", action="key_batch.create", target="activation_keys",
                                  metadata={"quantity": quantity, "permissions": sorted(permissions),
                                            "expires_at": expires_at},
                                  now=int(__import__("time").time()) if now is None else now)
            return {"batch_id": keys[0].batch_id if keys else "", "keys": [{"key_id": item.key_id, "key": item.plaintext,
                    "expires_at": item.expires_at} for item in keys]}
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
        request_hash = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        cache_key = (user_id, request_key); timestamp = int(__import__("time").time()) if now is None else now
        with self._volatile_key_batch_lock:
            for key in [k for k, value in self._volatile_key_batch_responses.items() if value[0] <= timestamp]:
                del self._volatile_key_batch_responses[key]
            cached = self._volatile_key_batch_responses.get(cache_key)
            if cached is not None:
                if cached[1] != request_hash:
                    raise ApiError(409, "idempotency_key_reused", "Idempotency-Key was reused for a different request")
                result = cached[2]
            else:
                result = operation(); self._volatile_key_batch_responses[cache_key] = (timestamp + self._VOLATILE_KEY_BATCH_TTL, request_hash, result)
        return ApiResponse(201, result, {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def _update_key(self, path, body, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.keys.distribute")
        team_id = self._team_id(headers, actor)
        if self.key_issuer is None or self.confirmations is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "key management is not configured")
        key_id = path[len("/admin/keys/"):].strip("/")
        payload = self._json_body(body)
        allowed = {"note", "permissions", "expires_at", "status"}
        if not payload or set(payload) - allowed:
            raise ApiError(400, "invalid_request", "key update fields are invalid")
        note = payload.get("note") if "note" in payload else None
        if note is not None and (not isinstance(note, str) or len(note) > 500):
            raise ApiError(400, "invalid_request", "note must be a string of at most 500 characters")
        permissions = None
        if "permissions" in payload:
            raw = payload["permissions"]
            if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
                raise ApiError(400, "invalid_request", "permissions must be a string array")
            permissions = frozenset(item.strip() for item in raw)
            if self.permission_catalog is not None:
                try:
                    permissions = self.permission_catalog.validate(permissions)
                except ValueError as exc:
                    raise ApiError(400, "invalid_request", str(exc)) from exc
        expires_at_provided = "expires_at" in payload
        expires_at = payload.get("expires_at")
        if expires_at is not None:
            try:
                expires_at = int(expires_at)
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "invalid_request", "expires_at must be an integer or null") from exc
        status = payload.get("status")
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
        def operation():
            try:
                preview = self.key_issuer.preview_update(key_id, note=note, permissions=permissions, expires_at=expires_at,
                    expires_at_provided=expires_at_provided, status=status, team_id=team_id)
                if preview["requires_confirmation"]:
                    self.confirmations.consume(self._header(headers, "X-Confirmation-Token"), action="key.update", target=key_id, now=now)
                return self.key_issuer.update(key_id, actor=actor, note=note, permissions=permissions, expires_at=expires_at,
                    expires_at_provided=expires_at_provided, status=status, team_id=team_id, now=now)
            except ValueError as exc:
                message = str(exc)
                if "confirmation" in message: raise ApiError(400, "confirmation_required", message) from exc
                if "not found" in message: raise ApiError(404, "key_not_found", message) from exc
                raise ApiError(409, "key_update_rejected", message) from exc
        return ApiResponse(200, self.idempotency.run(scope=f"admin:key-update:{team_id or '*'}:{actor}",
            request_key=request_key, request={"key_id": key_id, "team_id": team_id, **payload}, operation=operation, now=now))

    def _redeem_key(self, body, headers, *, now=None):
        if self.key_issuer is None or self.authorization is None:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        payload = self._json_body(body)
        key = payload.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ApiError(400, "invalid_request", "key is required")
        user_id, _ = self._session_user(headers, now=now)
        team_id = self._team_id(headers, user_id, required=self.platform is not None)
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key or len(request_key) > 256:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is invalid")
        key_hash = self.key_issuer.hash_key(key)

        def operation() -> dict[str, object]:
            try:
                grant = self.authorization.redeem_key(key_hash=key_hash, github_user_id=user_id, now=now,
                                                      team_id=team_id)
            except ValueError as exc:
                raise ApiError(409, "key_unavailable", str(exc)) from exc
            permissions = self.authorization.permissions(user_id, now=now, team_id=team_id)
            if self.audit is not None:
                self.audit.record(actor=f"github:{user_id}", action="key.redeem", target=grant.grant_id,
                                  metadata={"permissions": sorted(grant.permissions)},
                                  now=__import__("time").time() if now is None else now, team_id=team_id)

            def grant_payload(item) -> dict[str, object]:
                return {"grant_id": item.grant_id, "github_user_id": item.github_user_id,
                        "permissions": sorted(item.permissions), "expires_at": item.expires_at,
                        "state": item.status.value}

            return {"github_user_id": user_id, "permissions": sorted(permissions), "grant": grant_payload(grant),
                    "grants": [grant_payload(item) for item in self.authorization.grants(user_id, team_id=team_id)]}

        if self.idempotency is None:
            raise ApiError(503, "authorization_unavailable", "idempotency service is not configured")
        return ApiResponse(200, self.idempotency.run(
            scope="v1:keys:redeem", request_key=request_key,
            request={"key_hash": key_hash, "github_user_id": user_id},
            operation=operation, now=now,
        ))


class HarnessPackageOperationsMixin:
    """Package publication, catalog, and metadata adapter contract."""

    def package_search(self, path, headers, *, now=None):
        return self._search_admin_packages(path, headers, now=now)

    def package_download(self, package_id, body, headers, *, now=None):
        return self._issue_download_url(package_id, body, headers, now=now)

    def package_confirmation(self, path, headers, *, now=None):
        return self._issue_package_confirmation(path, headers, now=now)

    def package_status(self, path, body, headers, *, now=None):
        return self._change_package_status(path, body, headers, now=now)

    def package_metadata(self, path, body, headers, *, now=None):
        return self._update_package_metadata(path, body, headers, now=now)

    def package_upload_create(self, body, headers, *, now=None):
        return self._create_upload(body, headers, now=now)

    def package_upload_confirm(self, upload_id, body, headers, *, now=None):
        return self._confirm_upload(upload_id, body, headers, now=now)

    def _search_admin_packages(self, path, headers, *, now=None):
        if self.packages is None:
            raise ApiError(503, "distribution_unavailable", "package service is not configured")
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.packages.manage")
        query = parse_qs(urlsplit(path).query)
        package_id = query.get("package_id", [None])[0]
        status = query.get("status", [None])[0]
        try:
            limit = int(query.get("limit", ["50"])[0])
            offset = int(query.get("offset", ["0"])[0])
            packages, total = self.packages.search(package_id=package_id, status=status, limit=limit, offset=offset,
                                                   team_id=self._team_id(headers, actor, required=False))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        return ApiResponse(200, {"packages": [
            {"package_id": item.package_id, "version": item.version, "permission_node": item.permission_node,
             "archive_digest": item.archive_digest, "archive_size": item.archive_size, "status": item.status,
             "created_at": item.created_at} for item in packages], "total": total, "limit": limit, "offset": offset})

    def _issue_package_confirmation(self, path, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.packages.manage")
        if self.confirmations is None:
            raise ApiError(503, "admin_unavailable", "confirmation service is not configured")
        package_id, version, target = self._package_target(path, "/confirmation")
        if self.packages is None or self.packages.get(package_id, version, team_id=self._team_id(headers, actor)) is None:
            raise ApiError(404, "package_not_found", "package version was not found")
        token, confirmation = self.confirmations.issue(action="package.status", target=target, created_by=actor, now=now)
        return ApiResponse(200, {"confirmation_token": token, "action": confirmation.action, "target": confirmation.target,
                                 "expires_at": confirmation.expires_at},
                           {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def _issue_download_url(self, package_id, body, headers, *, now=None):
        if self.authorization is None or self.packages is None or self.download_tokens is None:
            raise ApiError(503, "distribution_unavailable", "package distribution is not configured")
        scheme, _, token = self._header(headers, "Authorization").partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            raise ApiError(401, "invalid_session", "session is invalid or expired")
        user_id = self.authentication.authenticate_session(token.strip(), now=now)
        if self.rate_limiter is not None:
            self.rate_limiter.check(scope="download-url", subject=user_id, limit=30, window=60,
                                    now=float(now) if now is not None else None)
        payload = self._json_body(body)
        version = payload.get("version")
        if not isinstance(version, str) or not version.strip() or not package_id:
            raise ApiError(400, "invalid_request", "package version is required")
        team_id = self._team_id(headers, user_id, required=self.platform is not None)
        package = self.packages.get(package_id, version.strip(), team_id=team_id)
        if package is None or package.status != "published":
            raise ApiError(404, "package_not_found", "published package version was not found")
        has_permission = self.authorization.allows(
            user_id,
            f"{package.permission_node}.download",
            now=now,
            team_id=team_id,
        )
        if not has_permission:
            raise ApiError(403, "permission_denied", "package permission is not granted")
        issued = self.download_tokens.issue(github_user_id=user_id, package_id=package_id, version=version.strip(),
                                            now=now)
        return ApiResponse(200, {"package_id": package_id, "version": version.strip(), "token": issued,
                                 "expires_in": self.download_tokens.max_ttl,
                                 "download_path": f"/v1/packages/{package_id}/download"})

    def _create_upload(self, body, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.packages.manage")
        team_id = self._team_id(headers, actor)
        if self.upload_store is None or self.direct_storage is None:
            raise ApiError(503, "distribution_unavailable", "direct upload is not configured")
        payload = self._json_body(body)
        try:
            metadata = payload.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be an object")
            draft = self.upload_store.create(package_id=str(payload.get("package_id", "")).strip(),
                                             version=str(payload.get("version", "")).strip(),
                                             size=int(payload.get("size")),
                                             content_type=str(payload.get("content_type", "application/octet-stream")),
                                             metadata=metadata, now=now, team_id=team_id)
            upload = self.direct_storage.create_upload(object_key=draft.object_key, size=draft.size,
                                                       content_type=draft.content_type,
                                                       expires_in=draft.expires_at - (__import__("time").time() if now is None else now))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        return ApiResponse(201, {"upload_id": draft.upload_id, "package_id": draft.package_id, "version": draft.version,
                                 "permission_node": draft.permission_node, "upload": upload,
                                 "expires_at": draft.expires_at, "created_by": actor})

    def _confirm_upload(self, upload_id, body, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.packages.manage")
        team_id = self._team_id(headers, actor)
        if self.upload_store is None or self.direct_storage is None or self.publisher is None:
            raise ApiError(503, "distribution_unavailable", "direct upload is not configured")
        draft = self.upload_store.get_pending(upload_id, now=now, team_id=team_id)
        payload = self._json_body(body)
        digest = payload.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ApiError(400, "invalid_request", "sha256 digest is required")
        try:
            self.direct_storage.confirm_upload(object_key=draft.object_key, digest=digest, size=draft.size)
            result = self.publisher.register_existing(package_id=draft.package_id, version=draft.version,
                                                      digest=digest, size=draft.size, metadata=draft.metadata, now=now,
                                                      team_id=team_id)
            self.upload_store.mark_confirmed(upload_id, now=now, team_id=team_id)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        if self.audit is not None:
            self.audit.record(actor=f"github:{actor}", action="package.publish",
                              target=f"{draft.package_id}@{draft.version}",
                              metadata={"archive_size": draft.size, "archive_digest": digest, "upload_id": upload_id},
                              now=__import__("time").time() if now is None else now)
        return ApiResponse(201, result.manifest)

    def _change_package_status(self, path, body, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.packages.manage")
        if self.packages is None or self.confirmations is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "package management is not configured")
        package_id, version, target = self._package_target(path, "/status")
        payload = self._json_body(body)
        status = payload.get("status")
        if status not in {"published", "disabled", "unpublished"}:
            raise ApiError(400, "invalid_request", "package status is invalid")
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation() -> dict[str, object]:
            self.confirmations.consume(self._header(headers, "X-Confirmation-Token"), action="package.status",
                                       target=target, now=now)
            try:
                self.packages.set_status(package_id, version, status, team_id=self._team_id(headers, actor))
            except ValueError as exc:
                raise ApiError(404, "package_not_found", str(exc)) from exc
            return {"ok": True, "package_id": package_id, "version": version, "status": status}

        return ApiResponse(200, self.idempotency.run(scope="admin:package-status", request_key=request_key,
                                                     request={"target": target, "status": status}, operation=operation,
                                                     now=now))

    def _update_package_metadata(self, path, body, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.packages.manage")
        if self.packages is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "package management is not configured")
        package_id, version, target = self._package_target(path, "/metadata")
        payload = self._json_body(body)
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ApiError(400, "invalid_request", "metadata must be an object")
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation() -> dict[str, object]:
            try:
                updated = self.packages.update_metadata(package_id, version, metadata,
                                                        team_id=self._team_id(headers, actor))
            except ValueError as exc:
                message = str(exc)
                code = "package_not_found" if "not found" in message else "invalid_request"
                raise ApiError(404 if code == "package_not_found" else 400, code, message) from exc
            return {"ok": True, "package_id": package_id, "version": version, "metadata": updated.metadata}

        return ApiResponse(200, self.idempotency.run(scope="admin:package-metadata", request_key=request_key,
                                                     request={"target": target, "metadata": metadata},
                                                     operation=operation, now=now))


class HarnessTemplateOperationsMixin:
    """Permission-template adapter contract."""

    def template_list(self, headers, *, now=None):
        return self._list_permission_templates(headers, now=now)

    def template_create(self, body, headers, *, now=None):
        return self._create_permission_template(body, headers, now=now)

    def template_confirmation(self, path, headers, *, now=None):
        return self._issue_template_confirmation(path, headers, now=now)

    def template_update(self, path, body, headers, *, now=None):
        return self._update_permission_template(path, body, headers, now=now)

    @staticmethod
    def _template_payload(template):
        return {"template_id": template.template_id, "name": template.name,
                "permissions": sorted(template.permissions), "expires_in": template.expires_in,
                "created_by": template.created_by, "created_at": template.created_at,
                "status": template.status}

    def _list_permission_templates(self, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        if self.permission_templates is None:
            raise ApiError(503, "admin_unavailable", "grant templates are not configured")
        team_id = self._team_id(headers, actor)
        return ApiResponse(200, {"templates": [self._template_payload(item) for item in
                                               self.permission_templates.list(active_only=False, team_id=team_id)]})

    def _issue_template_confirmation(self, path, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        if self.confirmations is None:
            raise ApiError(503, "admin_unavailable", "confirmation service is not configured")
        template_id = path[len("/admin/permission-templates/"):-len("/confirmation")].strip("/")
        token, confirmation = self.confirmations.issue(action="permission_template.disable", target=template_id,
                                                       created_by=actor, now=now)
        return ApiResponse(200, {"confirmation_token": token, "action": confirmation.action,
                                 "target": confirmation.target, "expires_at": confirmation.expires_at},
                           {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def _list_permissions(self, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.keys.distribute")
        if self.permission_catalog is None:
            raise ApiError(503, "admin_unavailable", "permission catalog is not configured")
        team_id = self._team_id(headers, actor)
        entries = self.permission_catalog.list(team_id=team_id, package_store=self.packages,
                                               template_store=self.permission_templates)
        templates = self.permission_templates.list(team_id=team_id) if self.permission_templates is not None else ()
        return ApiResponse(200, {
            "permissions": [entry.__dict__ for entry in entries],
            "templates": [{"template_id": item.template_id, "name": item.name,
                           "permissions": sorted(item.permissions), "expires_in": item.expires_in}
                          for item in templates],
        })

    def _create_permission_template(self, body, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        if self.permission_templates is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "grant templates are not configured")
        team_id = self._team_id(headers, actor)
        payload = self._json_body(body)
        name, permissions = payload.get("name"), payload.get("permissions")
        if not isinstance(name, str) or not isinstance(permissions, list):
            raise ApiError(400, "invalid_request", "name and permissions are required")
        expires_in = payload.get("expires_in")
        if expires_in is not None:
            try:
                expires_in = int(expires_in)
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "invalid_request", "expires_in must be an integer or null") from exc
        try:
            validated = self.permission_catalog.validate(permissions) if self.permission_catalog is not None else frozenset(permissions)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        if self.authorization_service is not None:
            self.authorization_service.require_grantable(actor, sorted(validated), team_id=team_id, now=now)
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation() -> dict[str, object]:
            try:
                template = self.permission_templates.create(name=name, permissions=validated, expires_in=expires_in,
                                                       created_by=actor, team_id=team_id, now=now)
            except ValueError as exc:
                raise ApiError(409 if "already exists" in str(exc) else 400, "invalid_request", str(exc)) from exc
            return self._template_payload(template)

        return ApiResponse(201, self.idempotency.run(scope=f"admin:permission-template-create:{team_id or '*'}",
                                      request_key=request_key,
                                      request={"name": name, "permissions": sorted(validated), "expires_in": expires_in},
                                      operation=operation, now=now))

    def _update_permission_template(self, path, body, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        if self.permission_templates is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "grant templates are not configured")
        team_id = self._team_id(headers, actor)
        template_id = path[len("/admin/permission-templates/"):].strip("/")
        payload = self._json_body(body)
        if not payload or set(payload) - {"name", "permissions", "expires_in", "status"}:
            raise ApiError(400, "invalid_request", "template update fields are invalid")
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
        def operation():
            try:
                current = self.permission_templates.get(template_id, active_only=False, team_id=team_id)
                if current is None: raise ValueError("grant template was not found")
                status = payload.get("status", current.status)
                if status not in {"active", "disabled"}: raise ValueError("grant template status is invalid")
                if status == "disabled" and current.status != "disabled":
                    self.confirmations.consume(self._header(headers, "X-Confirmation-Token"),
                                               action="permission_template.disable", target=template_id, now=now)
                updated = current
                if {"name", "permissions", "expires_in"} & set(payload):
                    permissions = payload.get("permissions", sorted(current.permissions))
                    if self.permission_catalog is not None: permissions = self.permission_catalog.validate(permissions)
                    updated = self.permission_templates.update(template_id, name=payload.get("name", current.name),
                        permissions=permissions, expires_in=payload.get("expires_in", current.expires_in), team_id=team_id)
                if status != updated.status:
                    self.permission_templates.set_status(template_id, status, team_id=team_id)
                    updated = self.permission_templates.get(template_id, active_only=False, team_id=team_id)
                return self._template_payload(updated)
            except ValueError as exc:
                if "confirmation" in str(exc): raise ApiError(400, "confirmation_required", str(exc)) from exc
                if "not found" in str(exc): raise ApiError(404, "template_not_found", str(exc)) from exc
                raise ApiError(400, "invalid_request", str(exc)) from exc
        return ApiResponse(200, self.idempotency.run(scope=f"admin:permission-template-update:{team_id or '*'}",
            request={"template_id": template_id, **payload}, request_key=request_key, operation=operation, now=now))


class HarnessAssignmentOperationsMixin:
    """Permission-assignment and audit adapter contract."""

    def assignment_search(self, path, headers, *, now=None):
        return self._search_grants(path, headers, now=now)

    def assignment_update(self, path, body, headers, *, now=None):
        return self._update_grant(path, body, headers, now=now)

    def assignment_revoke(self, path, headers, *, now=None):
        return self._revoke_grant(path, headers, now=now)

    def assignment_status(self, path, status, headers, *, now=None):
        return self._change_grant_status(path, status, headers, now=now)

    def assignment_extend(self, path, body, headers, *, now=None):
        return self._extend_grant(path, body, headers, now=now)

    def _search_grants(self, path, headers, *, now=None):
        if self.authorization is None:
            raise ApiError(503, "admin_unavailable", "authorization service is not configured")
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        query = parse_qs(urlsplit(path).query)
        try:
            limit = int(query.get("limit", ["50"])[0])
            offset = int(query.get("offset", ["0"])[0])
            grants = self.authorization.search_grants(github_user_id=query.get("github_user_id", [None])[0],
                status=query.get("status", [None])[0], limit=limit, offset=offset,
                team_id=self._team_id(headers, actor, required=False))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        return ApiResponse(200, {"grants": [{"grant_id": item.grant_id, "github_user_id": item.github_user_id,
            "permissions": sorted(item.permissions), "status": item.status.value, "expires_at": item.expires_at}
            for item in grants], "limit": limit, "offset": offset})

    def _issue_grant_confirmation(self, path, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        if self.confirmations is None:
            raise ApiError(503, "admin_unavailable", "confirmation service is not configured")
        marker = "/suspend/confirmation" if path.endswith("/suspend/confirmation") else "/confirmation"
        grant_id = path[len("/admin/permission-assignments/"):-len(marker)].strip("/")
        if not grant_id:
            raise ApiError(400, "invalid_request", "grant_id is required")
        action = "permission_assignment.suspend" if path.endswith("/suspend/confirmation") else "permission_assignment.revoke"
        token, confirmation = self.confirmations.issue(action=action, target=grant_id, created_by=actor, now=now)
        return ApiResponse(200, {"confirmation_token": token, "action": confirmation.action,
                                 "target": confirmation.target, "expires_at": confirmation.expires_at},
                           {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def _issue_grant_edit_confirmation(self, path, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        if self.confirmations is None or self.authorization is None:
            raise ApiError(503, "admin_unavailable", "grant management is not configured")
        grant_id = path[len("/admin/permission-assignments/"):-len("/edit-confirmation")].strip("/")
        team_id = self._team_id(headers, actor)
        try:
            self.authorization.preview_grant_update(grant_id, team_id=team_id)
        except ValueError as exc:
            raise ApiError(404, "grant_not_found", str(exc)) from exc
        target = f"{team_id or '*'}:{grant_id}"
        token, confirmation = self.confirmations.issue(action="permission_assignment.update", target=target,
                                                       created_by=actor, now=now)
        return ApiResponse(200, {"confirmation_token": token, "action": confirmation.action,
                                 "target": confirmation.target, "expires_at": confirmation.expires_at},
                           {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def _update_grant(self, path, body, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        if self.authorization is None or self.idempotency is None or self.confirmations is None:
            raise ApiError(503, "admin_unavailable", "grant management is not configured")
        grant_id = path[len("/admin/permission-assignments/"):].strip("/")
        team_id = self._team_id(headers, actor)
        payload = self._json_body(body)
        if not payload or set(payload) - {"permissions", "expires_at"}:
            raise ApiError(400, "invalid_request", "grant update fields are invalid")
        permissions = None
        if "permissions" in payload:
            raw = payload["permissions"]
            if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
                raise ApiError(400, "invalid_request", "permissions must be a string array")
            permissions = self.permission_catalog.validate(raw) if self.permission_catalog is not None else frozenset(raw)
        expires_at_provided = "expires_at" in payload
        expires_at = payload.get("expires_at")
        if expires_at is not None:
            try: expires_at = int(expires_at)
            except (TypeError, ValueError) as exc: raise ApiError(400, "invalid_request", "expires_at must be an integer or null") from exc
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key: raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")
        target = f"{team_id or '*'}:{grant_id}"
        def operation():
            try:
                preview = self.authorization.preview_grant_update(grant_id, permissions=permissions, expires_at=expires_at,
                    expires_at_provided=expires_at_provided, team_id=team_id)
                if preview["diff"]["permissions_removed"] or preview["diff"]["expiry_shortened"]:
                    self.confirmations.consume(self._header(headers, "X-Confirmation-Token"),
                                               action="permission_assignment.update", target=target, now=now)
                updated = self.authorization.update_grant(grant_id, permissions=permissions, expires_at=expires_at,
                    expires_at_provided=expires_at_provided, team_id=team_id, now=now)
            except ValueError as exc:
                message = str(exc)
                if "confirmation" in message: raise ApiError(400, "confirmation_required", message) from exc
                if "not found" in message: raise ApiError(404, "grant_not_found", message) from exc
                raise ApiError(409, "invalid_request", message) from exc
            return {"grant_id": updated.grant_id, "github_user_id": updated.github_user_id,
                    "permissions": sorted(updated.permissions), "expires_at": updated.expires_at,
                    "status": updated.status.value, "diff": preview["diff"]}
        return ApiResponse(200, self.idempotency.run(scope=f"admin:permission-assignment-update:{team_id or '*'}",
            request={"grant_id": grant_id, **payload}, request_key=request_key, operation=operation, now=now))

    def _revoke_grant(self, path, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        team_id = self._team_id(headers, actor)
        if self.authorization is None or self.confirmations is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "grant management is not configured")
        grant_id = path[len("/admin/permission-assignments/"):-len("/revoke")].strip("/")
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation():
            self.confirmations.consume(self._header(headers, "X-Confirmation-Token"),
                                       action="permission_assignment.revoke", target=grant_id, now=now)
            try:
                self.authorization.revoke_grant(grant_id, now=now, team_id=team_id)
            except ValueError as exc:
                raise ApiError(404, "grant_not_found", str(exc)) from exc
            return {"ok": True, "grant_id": grant_id, "status": "revoked"}

        return ApiResponse(200, self.idempotency.run(scope="admin:permission-assignment-revoke",
                                                     request_key=request_key,
                                                     request={"grant_id": grant_id}, operation=operation, now=now))

    def _change_grant_status(self, path, status, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        team_id = self._team_id(headers, actor)
        if self.authorization is None or self.confirmations is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "grant management is not configured")
        suffix = "/suspend" if status == "suspended" else "/" + status
        grant_id = path[len("/admin/permission-assignments/"):-len(suffix)].strip("/")
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation():
            action = "permission_assignment.suspend" if status == "suspended" else f"permission_assignment.{status}"
            self.confirmations.consume(self._header(headers, "X-Confirmation-Token"), action=action, target=grant_id, now=now)
            try:
                self.authorization.set_grant_status(grant_id, status, now=now, team_id=team_id)
            except ValueError as exc:
                raise ApiError(404, "grant_not_found", str(exc)) from exc
            return {"ok": True, "grant_id": grant_id, "status": status}

        return ApiResponse(200, self.idempotency.run(scope=f"admin:permission-assignment-{status}",
                                                     request_key=request_key,
                                                     request={"grant_id": grant_id}, operation=operation, now=now))

    def _extend_grant(self, path, body, headers, *, now=None):
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.permission_assignments.manage")
        team_id = self._team_id(headers, actor)
        if self.authorization is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "grant management is not configured")
        grant_id = path[len("/admin/permission-assignments/"):-len("/extend")].strip("/")
        request_key = self._header(headers, "Idempotency-Key").strip()
        expires_at = self._json_body(body).get("expires_at")
        try:
            expires_at = int(expires_at)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", "expires_at must be an integer") from exc
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation():
            try:
                self.authorization.extend_grant(grant_id, expires_at, now=now, team_id=team_id)
            except ValueError as exc:
                raise ApiError(404, "grant_not_found", str(exc)) from exc
            return {"ok": True, "grant_id": grant_id, "expires_at": expires_at}

        return ApiResponse(200, self.idempotency.run(scope="admin:permission-assignment-extend",
                                                     request_key=request_key,
                                                     request={"grant_id": grant_id, "expires_at": expires_at},
                                                     operation=operation, now=now))

    def audit_search(self, path, headers, *, now=None):
        return self._search_audit(path, headers, now=now)

    def audit_export(self, path, headers, *, now=None):
        return self._export_audit(path, headers, now=now)

    def _search_audit(self, path, headers, *, now=None):
        if self.audit is None:
            raise ApiError(503, "admin_unavailable", "audit service is not configured")
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.audit.read")
        query = parse_qs(urlsplit(path).query)
        try:
            limit = int(query.get("limit", ["100"])[0])
            offset = int(query.get("offset", ["0"])[0])
            events = self.audit.search(actor=query.get("actor", [None])[0], action=query.get("action", [None])[0],
                                       target=query.get("target", [None])[0], limit=limit, offset=offset,
                                       team_id=self._team_id(headers, actor, required=False))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        return ApiResponse(200, {"events": events, "limit": limit, "offset": offset})

    def _export_audit(self, path, headers, *, now=None):
        if self.audit is None:
            raise ApiError(503, "admin_unavailable", "audit service is not configured")
        actor = self.authenticated_admin(headers, now=now, required_node="team.default.audit.read")
        query = parse_qs(urlsplit(path).query)
        try:
            limit = int(query.get("limit", ["5000"])[0])
            if not 1 <= limit <= 5000:
                raise ValueError("audit export limit is invalid")
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        content = self.audit.export_csv(actor=query.get("actor", [None])[0], action=query.get("action", [None])[0],
                                        target=query.get("target", [None])[0], limit=limit,
                                        team_id=self._team_id(headers, actor, required=False))
        return ApiResponse(200, content, {"Content-Type": "text/csv; charset=utf-8",
                                          "Content-Disposition": "attachment; filename=access-audit.csv"})


class HarnessTeamOperationsMixin:
    """Team, application, member, and platform-user adapter contract."""

    def team_application_create(self, body, headers, *, now=None):
        return self._create_team_application(body, headers, now=now)

    def team_application_list(self, headers, *, now=None):
        return self._list_team_applications(headers, now=now)

    def platform_user_confirmation(self, path, headers, *, now=None):
        return self._issue_platform_user_confirmation(path, headers, now=now)

    def platform_user_template(self, path, body, headers, *, now=None):
        return self._set_platform_user_permission_template(path, body, headers, now=now)

    def platform_user_status(self, path, status, headers, *, now=None):
        return self._set_platform_user_status(path, status, headers, now=now)

    def team_confirmation(self, path, headers, *, now=None):
        return self._issue_team_confirmation(path, headers, now=now)

    def team_update(self, path, body, headers, *, now=None):
        return self._update_team_details(path, body, headers, now=now)

    def team_status(self, path, status, headers, *, now=None):
        return self._set_team_status(path, status, headers, now=now)

    def team_application_approve(self, path, headers, *, now=None):
        return self._approve_team_application(path, headers, now=now)

    def team_application_reject(self, path, body, headers, *, now=None):
        return self._reject_team_application(path, body, headers, now=now)

    def team_users_list(self, path, headers, *, now=None):
        return self._list_team_users(path, headers, now=now)

    def team_member_confirmation(self, path, body, headers, *, now=None):
        return self._issue_member_confirmation(path, body, headers, now=now)

    def team_member_template(self, path, body, headers, *, now=None):
        return self._change_member_permission_template(path, body, headers, now=now)

    def team_member_remove(self, path, headers, *, now=None):
        return self._remove_team_member(path, headers, now=now)

    def team_member_invite(self, path, body, headers, *, now=None):
        return self._invite_team_member(path, body, headers, now=now)

    def team_invitation_accept(self, path, headers, *, now=None):
        return self._accept_team_invitation(path, headers, now=now)

    def _create_team_application(self, body, headers, *, now=None):
        user_id, _ = self._platform_user(headers, now=now)
        payload = self._json_body(body)
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ApiError(400, "invalid_request", "name is required")
        try:
            result = self.platform.create_application(user_id, name, str(payload.get("description", "")),
                                                      now=__import__("time").time() if now is None else now)
        except ValueError as exc:
            raise ApiError(409, "invalid_request", str(exc)) from exc
        return ApiResponse(201, result)

    def _list_team_applications(self, headers, *, now=None):
        self._platform_admin(headers, now=now)
        applications, total = self.platform.applications()
        return ApiResponse(200, {"applications": applications, "total": total, "limit": 50, "offset": 0})

    def _issue_platform_user_confirmation(self, path, headers, *, now=None):
        self._platform_admin(headers, now=now)
        if self.confirmations is None:
            raise ApiError(503, "admin_unavailable", "confirmation service is not configured")
        user_id = path[len("/admin/platform/users/"):-len("/confirmation")].strip("/")
        if not user_id or self.platform.user(user_id) is None:
            raise ApiError(404, "not_found", "platform user was not found")
        token, confirmation = self.confirmations.issue(action="platform.user.update", target=user_id,
                                                       created_by=self._platform_admin(headers, now=now), now=now)
        return ApiResponse(200, {"confirmation_token": token, "action": confirmation.action,
                                 "target": confirmation.target, "expires_at": confirmation.expires_at},
                           {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def _set_platform_user_permission_template(self, path, body, headers, *, now=None):
        actor = self._platform_admin(headers, now=now)
        if self.platform is None or self.confirmations is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "platform service is not configured")
        user_id = path[len("/admin/platform/users/"):-len("/permission-template")].strip("/")
        template_name = str(self._json_body(body).get("permission_template", ""))
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation() -> dict[str, object]:
            try:
                self.confirmations.consume(self._header(headers, "X-Confirmation-Token"),
                                           action="platform.user.update", target=user_id, now=now)
                result = self.provisioning.set_user_permission_template(
                    user_id, template_name, actor=actor, now=__import__("time").time() if now is None else now,
                )
            except ValueError as exc:
                message = str(exc)
                if "confirmation" in message:
                    raise ApiError(400, "confirmation_required", message) from exc
                raise ApiError(409 if "Owner" in message or "invalid" in message else 404,
                               "invalid_request" if "Owner" in message or "invalid" in message else "not_found",
                               message) from exc
            if self.audit:
                self.audit.record(actor=f"github:{actor}", action="platform.user.permission_template", target=user_id,
                                  metadata={"permission_template": template_name},
                                  now=__import__("time").time() if now is None else now)
            return result

        return ApiResponse(200, self.idempotency.run(scope="admin:platform-user-permission-template",
                                                     request_key=request_key,
                                                     request={"user_id": user_id, "permission_template": template_name},
                                                     operation=operation,
                                                     now=now))

    def _set_platform_user_status(self, path, status, headers, *, now=None):
        actor = self._platform_admin(headers, now=now)
        if self.platform is None or self.confirmations is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "platform service is not configured")
        suffix = "/suspend" if status == "suspended" else "/activate"
        user_id = path[len("/admin/platform/users/"):-len(suffix)].strip("/")
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation() -> dict[str, object]:
            try:
                self.confirmations.consume(self._header(headers, "X-Confirmation-Token"),
                                           action="platform.user.update", target=user_id, now=now)
                result = self.platform.set_user_status(user_id, status,
                                                       now=__import__("time").time() if now is None else now)
            except ValueError as exc:
                message = str(exc)
                if "confirmation" in message:
                    raise ApiError(400, "confirmation_required", message) from exc
                raise ApiError(409 if "Owner" in message else 404,
                               "invalid_request" if "Owner" in message else "not_found", message) from exc
            if self.audit:
                self.audit.record(actor=f"github:{actor}", action="platform.user.status", target=user_id,
                                  metadata={"status": status}, now=__import__("time").time() if now is None else now)
            return result

        return ApiResponse(200, self.idempotency.run(scope="admin:platform-user-status", request_key=request_key,
                                                     request={"user_id": user_id, "status": status},
                                                     operation=operation,
                                                     now=now))

    def _issue_team_confirmation(self, path, headers, *, now=None):
        actor = self._platform_admin(headers, now=now)
        if self.confirmations is None:
            raise ApiError(503, "admin_unavailable", "confirmation service is not configured")
        team_id = path[len("/admin/teams/"):-len("/confirmation")].strip("/")
        if not team_id or not any(item["team_id"] == team_id for item in self.platform.teams()):
            raise ApiError(404, "not_found", "Team was not found")
        token, confirmation = self.confirmations.issue(action="platform.team.update", target=team_id,
                                                       created_by=actor, now=now)
        return ApiResponse(200, {"confirmation_token": token, "action": confirmation.action,
                                 "target": confirmation.target, "expires_at": confirmation.expires_at},
                           {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def _update_team_details(self, path, body, headers, *, now=None):
        actor = self._platform_admin(headers, now=now)
        if self.platform is None or self.confirmations is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "platform service is not configured")
        team_id = path[len("/admin/teams/"):-len("/details")].strip("/")
        payload = self._json_body(body)
        if set(payload) - {"name", "description"}:
            raise ApiError(400, "invalid_request", "Team update fields are invalid")
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation() -> dict[str, object]:
            try:
                self.confirmations.consume(self._header(headers, "X-Confirmation-Token"), action="platform.team.update",
                                           target=team_id, now=now)
                result = self.platform.update_team(team_id, name=str(payload.get("name", "")),
                                                   description=str(payload.get("description", "")),
                                                   now=__import__("time").time() if now is None else now)
            except ValueError as exc:
                message = str(exc)
                if "confirmation" in message:
                    raise ApiError(400, "confirmation_required", message) from exc
                raise ApiError(404, "not_found", message) from exc
            if self.audit:
                self.audit.record(actor=f"github:{actor}", action="platform.team.update", target=team_id,
                                  metadata={"fields": sorted(payload)}, now=__import__("time").time() if now is None else now)
            return result

        return ApiResponse(200, self.idempotency.run(scope="admin:platform-team-update", request_key=request_key,
                                                     request={"team_id": team_id, **payload}, operation=operation,
                                                     now=now))

    def _set_team_status(self, path, status, headers, *, now=None):
        actor = self._platform_admin(headers, now=now)
        if self.platform is None or self.confirmations is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "platform service is not configured")
        suffix = "/suspend" if status == "suspended" else "/archive"
        team_id = path[len("/admin/teams/"):-len(suffix)].strip("/")
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation() -> dict[str, object]:
            try:
                self.confirmations.consume(self._header(headers, "X-Confirmation-Token"), action="platform.team.update",
                                           target=team_id, now=now)
                result = self.platform.set_team_status(team_id, status,
                                                       now=__import__("time").time() if now is None else now)
            except ValueError as exc:
                message = str(exc)
                if "confirmation" in message:
                    raise ApiError(400, "confirmation_required", message) from exc
                raise ApiError(404, "not_found", message) from exc
            if self.audit:
                self.audit.record(actor=f"github:{actor}", action="platform.team.status", target=team_id,
                                  metadata={"status": status}, now=__import__("time").time() if now is None else now)
            return result

        return ApiResponse(200, self.idempotency.run(scope="admin:platform-team-status", request_key=request_key,
                                                     request={"team_id": team_id, "status": status},
                                                     operation=operation, now=now))

    def _approve_team_application(self, path, headers, *, now=None):
        reviewer = self._reviewer(headers, now=now)
        application_id = path[len("/admin/team-applications/"):-len("/approve")].strip("/")
        try:
                result = self.provisioning.approve_application(application_id, reviewer,
                                                       now=__import__("time").time() if now is None else now)
        except ValueError as exc:
            raise ApiError(409, "invalid_request", str(exc)) from exc
        if self.email_outbox is not None:
            applicant = self.platform.user(str(result["applicant_user_id"]))
            if applicant:
                self.email_outbox.enqueue(recipient=str(applicant.get("email", "")),
                                          subject="Team application approved",
                                          body=f"Your Team application was approved. Team ID: {result['team_id']}",
                                          now=now)
        return ApiResponse(200, result)

    def _reject_team_application(self, path, body, headers, *, now=None):
        reviewer = self._reviewer(headers, now=now)
        application_id = path[len("/admin/team-applications/"):-len("/reject")].strip("/")
        reason = str(self._json_body(body).get("reason", ""))
        try:
            result = self.platform.reject_application(application_id, reviewer, reason,
                                                      now=__import__("time").time() if now is None else now)
        except ValueError as exc:
            raise ApiError(409, "invalid_request", str(exc)) from exc
        if self.email_outbox is not None:
            application = next((item for item in self.platform.applications(status="rejected")[0]
                                if item["application_id"] == application_id), None)
            if application:
                applicant = self.platform.user(str(application["applicant_user_id"]))
                if applicant:
                    self.email_outbox.enqueue(recipient=str(applicant.get("email", "")),
                                              subject="Team application rejected", body=reason, now=now)
        return ApiResponse(200, result)

    def _list_team_users(self, path, headers, *, now=None):
        team_id = path[len("/admin/teams/"):-len("/members")].strip("/")
        self._authorize_team_admin(team_id, headers, now=now)
        return ApiResponse(200, {"members": self.authorization_service.team_members(team_id)})

    def _authorize_team_admin(self, team_id, headers, *, now=None):
        user_id, _ = self._platform_user(headers, now=now)
        if self.authorization_service is not None:
            try:
                self.authorization_service.require_system(user_id, "system.teams.manage", now=now)
            except ApiError as system_error:
                if system_error.code != "system_denied":
                    raise
                self.authorization_service.require_team(user_id, team_id, "users.manage", now=now)
        else:
            raise ApiError(503, "authorization_unavailable", "authorization service is not configured")
        return user_id

    def _issue_member_confirmation(self, path, body, headers, *, now=None):
        team_id, user_id = self._member_target(path, "/confirmation")
        actor = self._authorize_team_admin(team_id, headers, now=now)
        if self.confirmations is None:
            raise ApiError(503, "admin_unavailable", "confirmation service is not configured")
        requested = str(self._json_body(body).get("action", "")).strip()
        if requested not in {"permission_template", "remove"}:
            raise ApiError(400, "invalid_request", "member action is invalid")
        action = f"team.member.{requested}"
        target = f"{team_id}:{user_id}"
        token, confirmation = self.confirmations.issue(action=action, target=target, created_by=actor, now=now)
        return ApiResponse(200, {"confirmation_token": token, "action": action, "target": target,
                                 "expires_at": confirmation.expires_at},
                           {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def _change_member_permission_template(self, path, body, headers, *, now=None):
        team_id, user_id = self._member_target(path, "/permission-template")
        actor = self._authorize_team_admin(team_id, headers, now=now)
        if self.confirmations is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "member management is not configured")
        template_name = str(self._json_body(body).get("permission_template", "")).strip()
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation() -> dict[str, object]:
            try:
                self.confirmations.consume(self._header(headers, "X-Confirmation-Token"),
                                           action="team.member.permission_template",
                                           target=f"{team_id}:{user_id}", now=now)
                result = self.provisioning.set_member_permission_template(
                    team_id, user_id, template_name, now=__import__("time").time() if now is None else now,
                )
            except ValueError as exc:
                if "confirmation" in str(exc):
                    raise ApiError(400, "confirmation_required", str(exc)) from exc
                raise ApiError(409, "invalid_request", str(exc)) from exc
            if self.audit is not None:
                self.audit.record(actor=f"github:{actor}", action="team.member.permission_template",
                                  target=f"{team_id}:{user_id}",
                                  metadata={"permission_template": template_name},
                                  now=__import__("time").time() if now is None else now,
                                  team_id=team_id)
            return result

        return ApiResponse(200, self.idempotency.run(scope=f"admin:team-user-permission-template:{team_id}",
                                                     request_key=request_key,
                                                     request={"user_id": user_id, "permission_template": template_name},
                                                     operation=operation, now=now))

    def _remove_team_member(self, path, headers, *, now=None):
        team_id, user_id = self._member_target(path, "/remove")
        actor = self._authorize_team_admin(team_id, headers, now=now)
        if self.confirmations is None or self.idempotency is None:
            raise ApiError(503, "admin_unavailable", "member management is not configured")
        request_key = self._header(headers, "Idempotency-Key").strip()
        if not request_key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required")

        def operation() -> dict[str, object]:
            try:
                self.confirmations.consume(self._header(headers, "X-Confirmation-Token"), action="team.member.remove",
                                           target=f"{team_id}:{user_id}", now=now)
                result = self.provisioning.remove_member(team_id, user_id,
                                                     now=__import__("time").time() if now is None else now)
            except ValueError as exc:
                if "confirmation" in str(exc):
                    raise ApiError(400, "confirmation_required", str(exc)) from exc
                raise ApiError(409, "invalid_request", str(exc)) from exc
            if self.audit is not None:
                self.audit.record(actor=f"github:{actor}", action="team.member.remove", target=f"{team_id}:{user_id}",
                                  metadata={}, now=__import__("time").time() if now is None else now, team_id=team_id)
            return result

        return ApiResponse(200,
                           self.idempotency.run(scope=f"admin:team-member-remove:{team_id}", request_key=request_key,
                                                request={"user_id": user_id}, operation=operation, now=now))

    def _invite_team_member(self, path, body, headers, *, now=None):
        team_id = path[len("/admin/teams/"):-len("/invitations")].strip("/")
        user_id = self._authorize_team_admin(team_id, headers, now=now)
        payload = self._json_body(body)
        target = str(payload.get("user_id", "")).strip()
        template_name = str(payload.get("permission_template", "")).strip()
        if not target or not template_name:
            raise ApiError(400, "invalid_request", "user_id and permission_template are required")
        try:
            result = self.platform.invite(team_id, target, template_name, user_id,
                                          now=__import__("time").time() if now is None else now)
        except ValueError as exc:
            raise ApiError(409, "invalid_request", str(exc)) from exc
        if self.email_outbox is not None:
            target_user = self.platform.user(target)
            team = next((item for item in self.platform.teams() if item["team_id"] == team_id), None)
            if target_user and team:
                self.email_outbox.enqueue(recipient=str(target_user.get("email", "")),
                                          subject=f"Invitation to {team['name']}",
                                          body=f"You have been assigned template {template_name}. Invitation token: {result['token']}",
                                          now=now)
        return ApiResponse(201, result)

    def _accept_team_invitation(self, path, headers, *, now=None):
        user_id, _ = self._platform_user(headers, now=now)
        token = path[len("/admin/team-invitations/"):-len("/accept")].strip("/")
        try:
            result = self.provisioning.accept_invitation(token, user_id, now=__import__("time").time() if now is None else now)
        except ValueError as exc:
            raise ApiError(409, "invalid_request", str(exc)) from exc
        return ApiResponse(200, result)
