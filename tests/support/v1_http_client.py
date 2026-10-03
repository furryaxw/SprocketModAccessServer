"""直接驱动 ASGI app 的小客户端：返回状态码、响应头与响应体。"""

from __future__ import annotations

import asyncio
import json
from typing import Any


def call(app, method: str, path: str, *, headers: dict[str, str] | None = None,
         scheme: str = "https", host: str = "server.test", query: str = "",
         body: bytes = b"") -> tuple[int, dict[str, str], bytes]:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    messages: list[dict[str, object]] = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, Any]] = []

    async def receive():
        if messages:
            return messages.pop(0)
        # 请求之后不断开：流式响应一旦看到 disconnect 就会被中间件取消，body 会变成空的。
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app({
        "type": "http", "method": method, "path": path, "headers": raw_headers,
        "query_string": query.encode(), "client": ("test", 1), "server": (host, 80),
        "scheme": scheme, "http_version": "1.1",
    }, receive, send))
    start = next(item for item in sent if item["type"] == "http.response.start")
    payload = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    response_headers = {key.decode().lower(): value.decode() for key, value in start.get("headers", [])}
    return int(start["status"]), response_headers, payload


def json_call(app, method: str, path: str, **options) -> tuple[int, Any]:
    status, _headers, payload = call(app, method, path, **options)
    return status, json.loads(payload)
