from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from contextlib import suppress
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .authorization import authorize_resource_operation
from .v1_http import V1HttpAdapter
from .ws_hub import ConnectionState, WebSocketHub
from ..domain.errors import ApiError
from ..domain.protocol import PROTOCOL_VERSION, ServerInfo
from ..infrastructure.configuration.paths import admin_ui_dist
from ..infrastructure.events import ResourceChanged
from ..infrastructure.messaging.email_worker import run_email_worker
from ..infrastructure.network import ResourceRegistry, WsEnvelope, json_dumps, response_payload
from ..modules.system.authentication import AuthenticationService

logger = logging.getLogger(__name__)


def create_app(server_info: ServerInfo, authentication: AuthenticationService, **api_options: Any) -> Starlette:
    email_sender = api_options.pop("email_sender", None)
    email_poll_interval = float(api_options.pop("email_poll_interval", 15.0))
    email_outbox = api_options.pop("email_outbox", None)
    module_context = api_options.pop("module_context", None)
    modules = api_options.pop("modules", ())
    resource_registry = getattr(module_context, "resources", None) or ResourceRegistry()
    authorization_service = _service(module_context, "authorization_service")
    hub = WebSocketHub(authorization_service=authorization_service)

    events = _service(module_context, "events")
    if events is not None:
        def _on_resource_changed(event: ResourceChanged) -> None:
            try:
                asyncio.create_task(hub.broadcast_resource_changed(event))
            except RuntimeError:
                pass

        events.subscribe(ResourceChanged, _on_resource_changed)

    @asynccontextmanager
    async def lifespan(_app):
        stop = asyncio.Event()
        task = None
        if email_outbox is not None and email_sender is not None:
            task = asyncio.create_task(
                run_email_worker(email_outbox, email_sender, interval=email_poll_interval, stop=stop)
            )
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                with suppress(asyncio.CancelledError):
                    await task

    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "protocol_version": PROTOCOL_VERSION})

    async def admin_page(_request: Request) -> Response:
        built = admin_ui_dist() / "index.html"
        if not built.is_file():
            return JSONResponse(
                {"code": "admin_ui_unavailable", "message": "built admin client is not installed"},
                status_code=503,
            )
        return FileResponse(built)

    async def admin_root(_request: Request) -> Response:
        # 前后端同源同端口：根路径把人送到管理端，客户端的 /v1 与 /ws 仍在这个端口上。
        return RedirectResponse("/admin/", status_code=307)

    async def admin_shell(request: Request) -> Response:
        # dist 根部的静态文件（favicon 等）没有单独挂载点，先按路径找文件，再回落到 SPA 外壳。
        built_root = admin_ui_dist()
        candidate = (built_root / request.path_params.get("path", "")).resolve()
        if built_root.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        if "text/html" not in request.headers.get("accept", ""):
            return JSONResponse(
                {"code": "not_found", "message": "HTTP business APIs are replaced by WebSocket"},
                status_code=404,
            )
        return await admin_page(request)

    async def websocket_dispatch(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin", "").strip()
        if origin and not _origin_allowed(websocket, origin):
            logger.warning("websocket origin rejected origin=%s host=%s", origin, websocket.headers.get("host", ""))
            await websocket.close(code=1008)
            return
        await websocket.accept()
        logger.debug("websocket accepted path=%s origin=%s query_token_present=%s",
                     websocket.url.path, origin, bool(websocket.query_params.get("access_token", "").strip()))
        request_headers = dict(websocket.headers)
        access_token = websocket.query_params.get("access_token", "").strip()
        if access_token and "authorization" not in {key.casefold() for key in request_headers}:
            request_headers["authorization"] = f"Bearer {access_token}"
        state = _connection_state(websocket, request_headers)
        await hub.add(state)
        try:
            while True:
                envelope: WsEnvelope | None = None
                try:
                    raw = await websocket.receive_text()
                except WebSocketDisconnect:
                    return
                try:
                    envelope = WsEnvelope.parse(raw)
                    logger.debug("websocket dispatch start action=%s node=%s request_id=%s",
                                 envelope.action, envelope.node, envelope.request_id)
                    match = resource_registry.resolve(envelope.action, envelope.node)
                    if match is None:
                        raise KeyError(f"resource operation is not registered: {envelope.action} {envelope.node}")
                    operation_headers = _operation_headers(request_headers, envelope.headers)
                    authorize_resource_operation(
                        match,
                        authentication=authentication,
                        authorization=authorization_service,
                        headers=operation_headers,
                        now=None,
                        data=envelope.data,
                    )
                    result = match.operation.handler(
                        envelope.data,
                        operation_headers,
                        None,
                    )
                    if (
                            envelope.node == "system.authentication.team_context"
                            and envelope.action == "select"
                    ):
                        selected_team = str(envelope.data.get("team_id", "")).strip()
                        if selected_team:
                            request_headers["x-team-id"] = selected_team
                    payload = result.payload if hasattr(result, "payload") else result
                    ok = int(getattr(result, "status", 200)) < 400
                    if not isinstance(payload, dict):
                        payload = {"value": payload}
                    await websocket.send_text(
                        json_dumps(response_payload(envelope, ok=ok, data=payload))
                    )
                    logger.debug("websocket dispatch completed action=%s node=%s request_id=%s status=%s",
                                 envelope.action, envelope.node, envelope.request_id, getattr(result, "status", 200))
                except ApiError as exc:
                    logger.warning("websocket dispatch api_error action=%s node=%s code=%s status=%d",
                                   getattr(envelope, "action", None), getattr(envelope, "node", None),
                                   exc.code, exc.status)
                    await websocket.send_text(
                        json_dumps(response_payload(envelope, ok=False, error=exc.payload()))
                    )
                except (KeyError, ValueError) as exc:
                    logger.warning("websocket dispatch invalid_request action=%s node=%s error=%s",
                                   getattr(envelope, "action", None), getattr(envelope, "node", None), exc)
                    await websocket.send_text(
                        json_dumps(
                            response_payload(
                                envelope,
                                ok=False,
                                error={"code": "invalid_request", "message": str(exc)},
                            )
                        )
                    )
        finally:
            await hub.remove(state)

    def _connection_state(websocket: WebSocket, headers: dict[str, str] | None = None) -> ConnectionState:
        connection_headers = headers or dict(websocket.headers)
        user_id = None
        auth_header = connection_headers.get("authorization", "").strip()
        if auth_header:
            scheme, _, token = auth_header.partition(" ")
            if scheme.casefold() == "bearer" and token.strip():
                try:
                    user_id = authentication.authenticate_session(token.strip())
                except Exception:
                    user_id = None
        return ConnectionState(websocket=websocket, user_id=user_id)

    def _operation_headers(base: dict[str, str], message_headers: dict[str, str]) -> dict[str, str]:
        # 逐请求可带 Team 作用域，连接上的 x-team-id 只是默认值：并发操作不靠 select 顺序。
        # header 名大小写不敏感：先删掉同名的其它大小写，再写规范的小写名，
        # 否则读取端按第一个匹配取值时会拿到连接上的旧值。
        headers = dict(base)
        for key, value in message_headers.items():
            name = key.strip().casefold()
            if name not in {"idempotency-key", "x-confirmation-token", "x-team-id"}:
                continue
            for existing in [item for item in headers if item.casefold() == name]:
                headers.pop(existing, None)
            headers[name] = value
        return headers

    async def github_callback(request: Request) -> Response:
        logger.debug("github callback http received code_present=%s state_present=%s origin=%s referer=%s",
                     bool(request.query_params.get("code", "")),
                     bool(request.query_params.get("state", "")),
                     request.headers.get("origin", ""),
                     request.headers.get("referer", ""))
        try:
            result = authentication.complete_github_web_flow(
                request.query_params.get("code", ""),
                request.query_params.get("state", ""),
            )
            message = {"ok": True, "payload": result.payload()}
            logger.debug("github callback completed user_id=%s", result.github_user_id)
        except ApiError as exc:
            logger.warning("github callback api_error code=%s status=%d", exc.code, exc.status)
            message = {"ok": False, "error": exc.payload()}
        except Exception:
            logger.exception("github callback unexpected failure")
            message = {
                "ok": False,
                "error": {
                    "code": "github_oauth_failed",
                    "message": "GitHub login could not be completed",
                },
            }
        payload = json.dumps(message, ensure_ascii=False)
        safe_payload = payload.replace("</", "<\\/")
        frontend_origins = _configured_frontend_origins(request)
        logger.debug("github callback posting result ok=%s frontend_origins=%s", message.get("ok"), frontend_origins)
        # Fix: local Vite and backend origins differ, so callback delivery targets configured frontend origins.
        messages = "".join(
            f"try{{window.opener?.postMessage({safe_payload},{json.dumps(origin)});}}catch(_error){{}}"
            for origin in frontend_origins
        )
        html = (
            "<!doctype html><meta charset='utf-8'><title>GitHub login</title>"
            f"<script>{messages} window.close();</script>"
            "<p>Login completed. You can close this window.</p>"
        )
        return Response(html, status_code=200, media_type="text/html")

    built_assets = admin_ui_dist() / "assets"
    v1 = V1HttpAdapter(module_context)
    routes = [
        Route("/", admin_root, methods=["GET"]),
        Route("/healthz", health, methods=["GET"]),
        Route("/v1/server-info", v1.server_info, methods=["GET"]),
        Route("/v1/key-status", v1.key_status, methods=["GET"]),
        Route("/v1/auth/github/exchange", v1.github_exchange, methods=["POST"]),
        Route("/v1/auth/service/exchange", v1.service_exchange, methods=["POST"]),
        Route("/v1/auth/session/revoke", v1.revoke_session, methods=["POST"]),
        Route("/v1/keys/redeem", v1.redeem_key, methods=["POST"]),
        Route("/v1/invitations/accept", v1.accept_invitation, methods=["POST"]),
        Route("/v1/teams", v1.teams, methods=["GET"]),
        Route("/v1/entitlements", v1.entitlements, methods=["GET"]),
        Route("/v1/packages", v1.packages, methods=["GET"]),
        Route("/v1/packages/{package_id}/download", v1.download, methods=["GET"]),
        Route("/v1/package-uploads/{object_key}", v1.package_upload, methods=["PUT"]),
        Route("/admin", admin_page, methods=["GET"]),
        Route("/admin/", admin_page, methods=["GET"]),
        Route("/admin/{path:path}", admin_shell, methods=["GET"]),
        Route("/v1/auth/github/callback", github_callback, methods=["GET"]),
        WebSocketRoute("/ws", websocket_dispatch),
        WebSocketRoute("/api/ws", websocket_dispatch),
    ]
    if built_assets.is_dir():
        routes.insert(1, Mount("/admin/assets", app=StaticFiles(directory=built_assets), name="admin-assets"))
    async def log_http_request(request: Request, call_next):
        started = time.perf_counter()
        logger.debug("http request start method=%s path=%s query_present=%s",
                     request.method, request.url.path, bool(request.url.query))
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("http request failed method=%s path=%s", request.method, request.url.path)
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.debug("http request completed method=%s path=%s status=%d elapsed_ms=%.2f",
                     request.method, request.url.path, response.status_code, elapsed_ms)
        return response

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=log_http_request)],
    )
    app.add_exception_handler(ApiError, _api_error_response)
    app.state.module_context = module_context
    app.state.modules = tuple(modules)
    return app


async def _api_error_response(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(exc.payload(), status_code=exc.status, headers=dict(exc.headers))


def _origin_allowed(websocket: WebSocket, origin: str) -> bool:
    configured = {
        value.strip()
        for value in os.environ.get("SMAS_WS_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    }
    configured.update(_configured_frontend_origins(websocket))
    host = websocket.headers.get("host", "").strip()
    if host:
        configured.add(f"{'https' if websocket.url.scheme == 'wss' else 'http'}://{host}")
    return origin in configured


def _configured_frontend_origins(request: Request | WebSocket) -> tuple[str, ...]:
    configured = tuple(
        value.strip()
        for value in os.environ.get("SMAS_FRONTEND_ORIGIN", "").split(",")
        if value.strip()
    )
    if configured:
        return configured
    return (f"{request.url.scheme}://{request.url.netloc}",)


def _service(context: Any, name: str) -> Any:
    if context is None:
        return None
    getter = getattr(context, "service", None)
    if getter is None:
        return None
    try:
        return getter(name)
    except Exception:
        return None
