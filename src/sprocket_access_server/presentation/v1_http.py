from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from .authorization import authorize_resource_operation
from ..domain.errors import ApiError
from ..domain.resources import DEFAULT_TEAM_ID, SYSTEM_TEAM_ID, TEMPLATE_TEAM_ID, split_package_id

logger = logging.getLogger(__name__)

# 客户端接受的下载地址只有两类：https 的任意主机，或 http 的 loopback。
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _usable_download_origin(origin: str) -> bool:
    """这个 origin 是否属于客户端会接受的两类地址。"""
    parsed = urlsplit(origin)
    if not parsed.scheme or not parsed.hostname:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS


def _iso_now(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _chunks(stream: Any) -> Iterator[bytes]:
    """按块转发归档字节，并在结束时关掉底层流：句柄不跟着响应对象一起漂。"""
    try:
        while chunk := stream.read(1024 * 1024):
            yield chunk
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()


class V1HttpAdapter:
    """HTTP adapter for the private-server v1 client contract."""

    def __init__(self, context: Any):
        self.context = context

    async def server_info(self, _request: Request) -> Response:
        return self._json(self._service("server_info").payload())

    async def key_status(self, request: Request) -> Response:
        result = self._dispatch("read", "system.key_status", request)
        return self._result(result)

    async def github_exchange(self, request: Request) -> Response:
        request.state.v1_body = await self._body(request)
        result = self._dispatch("exchange", "system.authentication.github", request)
        return self._result(result)

    async def service_exchange(self, request: Request) -> Response:
        request.state.v1_body = await self._body(request)
        result = self._dispatch("exchange", "system.authentication.service", request)
        return self._result(result)

    async def revoke_session(self, request: Request) -> Response:
        request.state.v1_body = await self._body(request)
        result = self._dispatch("revoke", "system.authentication.session", request)
        return self._result(result)

    async def redeem_key(self, request: Request) -> Response:
        request.state.v1_body = await self._body(request)
        result = self._dispatch("distribute", "system.keys.redeem", request)
        return self._result(result)

    async def accept_invitation(self, request: Request) -> Response:
        request.state.v1_body = await self._body(request)
        result = self._dispatch("accept", "system.team_invitations", request)
        return self._result(result)

    async def teams(self, request: Request) -> Response:
        """团队发现：会话决定身份，客户端据此展示全部可用 Team 与空状态。"""
        user_id = self._authenticated_user(request)
        teams, _readable = self._visible_teams(user_id, now=int(time.time()))
        return self._json({"teams": teams})

    async def entitlements(self, request: Request) -> Response:
        user_id = self._authenticated_user(request)
        now = int(time.time())
        authorization = self._service("authorization")
        authorization_service = self._service("authorization_service")
        _teams, readable = self._visible_teams(user_id, now=now)
        grants = authorization.grants(user_id, team_id=None)
        permissions = set(authorization_service.context(user_id, team_id=None, now=now).effective_permissions)
        for team_id in readable:
            permissions.update(
                authorization_service.context(user_id, team_id=team_id, now=now).effective_permissions
            )
        return self._json({
            "github_user_id": user_id,
            "permissions": sorted(permissions),
            "grants": [self._grant_payload(item) for item in grants],
        })

    async def packages(self, request: Request) -> Response:
        """一次返回我有权读取的全部 Team 的包：包挂在它所属的 Team 下面。

        索引只描述包：Team 列表里的保留 workspace 既不装包也不出现在这里，
        其余 Team 一律带 `packages` 键（没有已发布包时是空数组）。
        """
        user_id = self._authenticated_user(request)
        now = int(time.time())
        teams, readable = self._visible_teams(user_id, now=now)
        signing_key = self._service("signing_key")
        signing_key_id = self._service("signing_key_id")
        download_base_url = self._download_base_url(request)
        buckets: dict[str, dict[str, Any]] = {}
        for team in teams:
            if team["team_id"] in {SYSTEM_TEAM_ID, TEMPLATE_TEAM_ID}:
                continue
            buckets[team["team_id"]] = {**team, "packages": []}
        for record in self._service("packages").records(team_ids=tuple(readable)):
            bucket = buckets.get(record.team_id)
            if bucket is None:
                continue
            bucket["packages"].append(
                record.signed_entry(signing_key, signing_key_id, download_base_url=download_base_url)
                if signing_key is not None and signing_key_id
                else record.entry(download_base_url=download_base_url)
            )
        server_info = self._service("server_info")
        return self._json({
            "schema_version": 1,
            "generated_at": _iso_now(now),
            "server": {"server_id": server_info.server_id, "name": server_info.name},
            "teams": list(buckets.values()),
        })

    async def download(self, request: Request) -> Response:
        user_id = self._authenticated_user(request)
        package_id = request.path_params["package_id"]
        version = str(request.query_params.get("version", "")).strip()
        if not version:
            raise ApiError(400, "invalid_request", "version is required")
        # Team 由包 id 的第一段推出：客户端不需要任何工作区参数。
        try:
            team_id, _mod = split_package_id(package_id)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        package = self._service("packages").get(package_id, version, team_id=team_id)
        if package is None or package.status != "published":
            raise ApiError(404, "package_not_found", "published package version was not found")
        # 下载时重新判定授权：会话有效且此刻仍持有该 Team 的下载权限。
        if not self._can_download(user_id, package, team_id):
            raise ApiError(403, "download_denied", "package download is not granted")
        storage = self._service("direct_storage")
        if storage is None:
            raise ApiError(503, "distribution_unavailable", "package storage is not configured")
        # 对象存储把下载交给桶的短寿命签名 URL（客户端据此校验来源）；本地存储由服务端代理字节。
        redirect = storage.download_redirect(package.archive_digest)
        if redirect:
            return RedirectResponse(redirect, status_code=302, headers={"Cache-Control": "no-store"})
        try:
            stream = storage.open(package.archive_digest)
        except (OSError, FileNotFoundError) as exc:
            raise ApiError(404, "package_not_found", "package archive is unavailable") from exc
        return StreamingResponse(
            _chunks(stream),
            media_type="application/octet-stream",
            headers={
                "Content-Length": str(package.archive_size),
                "Cache-Control": "no-store",
            },
        )

    async def package_upload(self, request: Request) -> Response:
        storage = self._service("direct_storage")
        write_upload = getattr(storage, "write_upload", None)
        if write_upload is None:
            raise ApiError(404, "not_found", "direct upload endpoint is unavailable")
        try:
            write_upload(
                object_key=request.path_params["object_key"],
                content=await request.body(),
            )
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(404, "upload_not_found", "upload target was not found") from exc
        return Response(status_code=204)

    def _dispatch(self, action: str, node: str, request: Request):
        data = getattr(request.state, "v1_body", {})
        headers = dict(request.headers)
        # Key 兑换与服务号兑换都不消费 Team 上下文，默认 Team 头对它们没有意义（注入只会造成误导性日志）。
        if node not in {
            "system.keys.redeem",
            "system.authentication.service",
        } and "x-team-id" not in {key.casefold() for key in headers}:
            headers["x-team-id"] = self._team_id(request)
        try:
            logger.debug("v1 resource dispatch start method=%s path=%s action=%s node=%s",
                         request.method, request.url.path, action, node)
            match = self.context.resources.resolve(action, node)
            if match is None:
                raise KeyError(f"resource operation is not registered: {action} {node}")
            authorize_resource_operation(
                match,
                authentication=self._service("authentication"),
                authorization=self._service("authorization_service"),
                headers=headers,
                now=None,
            )
            result = match.operation.handler(data, headers, None)
            logger.debug("v1 resource dispatch completed method=%s path=%s action=%s node=%s status=%s",
                         request.method, request.url.path, action, node, getattr(result, "status", 200))
            return result
        except (KeyError, ValueError) as exc:
            logger.warning("v1 resource dispatch invalid_request method=%s path=%s action=%s node=%s error=%s",
                           request.method, request.url.path, action, node, exc)
            raise ApiError(400, "invalid_request", str(exc)) from exc

    def _authenticated_user(self, request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            raise ApiError(401, "invalid_session", "session is invalid or expired")
        return self._service("authentication").authenticate_session(token.strip())

    def _visible_teams(self, user_id: str, *, now: int) -> tuple[list[dict[str, Any]], list[str]]:
        """会话可见的全部 Team，以及其中可读包列表的 Team（索引与权限用同一份判定）。"""
        authorization_service = self._service("authorization_service")
        teams: list[dict[str, Any]] = []
        readable: list[str] = []
        for item in authorization_service.memberships(user_id):
            team_id = str(item.get("team_id", ""))
            if not team_id:
                continue
            teams.append({"team_id": team_id, "name": str(item.get("name", ""))})
            if authorization_service.allows(user_id, f"team.{team_id}.packages.read", now=now):
                readable.append(team_id)
        return teams, readable

    def _team_id(self, request: Request) -> str:
        # 仅用于 WS 资源调度：客户端 HTTP 契约已无工作区参数。
        selected = request.headers.get("x-team-id", "").strip()
        return selected or DEFAULT_TEAM_ID

    def _can_download(self, user_id: str, package: Any, team_id: str) -> bool:
        authorization = self._service("authorization_service")
        now = int(time.time())
        if any(
                authorization.allows(user_id, permission, now=now, team_id=team_id)
                for permission in (
                        f"{package.permission_node}.download",
                        f"team.{team_id}.packages.download",
                )
        ):
            return True
        return False

    @staticmethod
    def _grant_payload(grant: Any) -> dict[str, Any]:
        return {
            "grant_id": grant.grant_id,
            "github_user_id": grant.github_user_id,
            "permissions": sorted(grant.permissions),
            "expires_at": grant.expires_at,
            "state": grant.status.value,
        }

    @staticmethod
    async def _body(request: Request) -> dict[str, Any]:
        if request.method == "GET":
            return {}
        raw = await request.body()
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "invalid_request", "request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ApiError(400, "invalid_request", "request body must be an object")
        return value

    def _service(self, name: str) -> Any:
        if self.context is None:
            raise ApiError(503, "distribution_unavailable", "server service is not configured")
        try:
            return self.context.service(name)
        except (KeyError, RuntimeError) as exc:
            raise ApiError(503, "distribution_unavailable", "server service is not configured") from exc

    def _download_base_url(self, request: Request) -> str:
        """索引里下载端点的基准 origin。

        `SMAS_PUBLIC_BASE_URL` 配了就一律用它：TLS 通常在反向代理上终止，服务端看到的请求 origin
        往往不是用户访问的那个（明文 http、主机名还没有端口），而配置是运维明确声明的对外入口。
        没配时才退回请求 origin；退回的这个若不是客户端会接受的类型，记一条 WARNING——否则失败
        只会表现为客户端拒绝该地址。
        """
        configured = self._configured_public_base_url()
        if configured:
            return configured
        origin = f"{request.url.scheme}://{request.url.netloc}"
        if not _usable_download_origin(origin):
            logger.warning(
                "package index download origin is not accepted by clients origin=%s; "
                "set SMAS_PUBLIC_BASE_URL to the externally reachable https origin",
                origin,
            )
        return origin

    def _configured_public_base_url(self) -> str:
        """发布器上配置的服务端公开 origin（`SMAS_PUBLIC_BASE_URL`）；未配置时为空串。"""
        if self.context is None:
            return ""
        try:
            publisher = self.context.service("publisher")
        except (KeyError, RuntimeError):
            return ""
        return str(getattr(publisher, "download_base_url", "") or "").strip()

    @staticmethod
    def _json(value: dict[str, Any], status_code: int = 200) -> JSONResponse:
        return JSONResponse(value, status_code=status_code)

    @staticmethod
    def _result(result: Any) -> Response:
        status = int(getattr(result, "status", 200))
        payload = getattr(result, "payload", result)
        headers = dict(getattr(result, "headers", {}) or {})
        return JSONResponse(payload, status_code=status, headers=headers)
