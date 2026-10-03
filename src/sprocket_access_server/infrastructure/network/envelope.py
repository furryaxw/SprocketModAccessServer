from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from collections.abc import Mapping
from typing import Any


@dataclass(frozen=True)
class WsEnvelope:
    action: str
    node: str
    data: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    request_id: str = ""

    @classmethod
    def parse(cls, raw: str) -> "WsEnvelope":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("message must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("message must be a JSON object")
        action = value.get("action")
        node = value.get("node")
        data = value.get("data", {})
        headers = value.get("headers", {})
        request_id = value.get("request_id", "")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("action is required")
        if not isinstance(node, str) or not node.strip():
            raise ValueError("node is required")
        if not isinstance(data, dict):
            raise ValueError("data must be an object")
        if not isinstance(headers, dict):
            raise ValueError("headers must be an object")
        normalized_headers = {}
        for key, header_value in headers.items():
            if not isinstance(key, str) or not isinstance(header_value, str):
                raise ValueError("headers must be a string map")
            normalized_headers[key] = header_value
        if request_id is None:
            request_id = ""
        if not isinstance(request_id, str):
            raise ValueError("request_id must be a string")
        return cls(
            action=action,
            node=node,
            data=data,
            headers=normalized_headers,
            request_id=request_id,
        )


def response_payload(
        envelope: WsEnvelope | None,
        *,
        ok: bool,
        data: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "ok": ok,
        "action": envelope.action if envelope is not None else "",
        "node": envelope.node if envelope is not None else "",
        "data": data or {},
        "request_id": envelope.request_id if envelope is not None else "",
    }
    if error is not None:
        payload["error"] = error
    return payload


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "keys"):
        return {key: value[key] for key in value.keys()}
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def broadcast_payload(
        *,
        action: str,
        node: str,
        data: dict[str, Any] | None = None,
        kind: str = "resource.changed",
) -> dict[str, Any]:
    return {
        "kind": kind,
        "action": action,
        "node": node,
        "data": data or {},
    }
