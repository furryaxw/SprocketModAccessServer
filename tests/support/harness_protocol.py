from __future__ import annotations

import json
from typing import Any

from sprocket_access_server.domain.errors import ApiError


class HarnessProtocolMixin:
    """Transport-neutral parsing helpers shared by harness operations."""

    @staticmethod
    def _header(headers: dict[str, str], name: str) -> str:
        expected = name.casefold()
        return next((str(value) for key, value in headers.items() if str(key).casefold() == expected), "")

    @staticmethod
    def _json_body(body: bytes) -> dict[str, Any]:
        if not body:
            raise ApiError(400, "invalid_request", "JSON request body is required")
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ApiError(400, "invalid_request", "JSON request body must be an object")
        return value

    @staticmethod
    def _member_target(path: str, suffix: str) -> tuple[str, str]:
        value = path[len("/admin/teams/"):-len(suffix)].strip("/")
        team_id, marker, user_id = value.partition("/members/")
        if not marker or not team_id or not user_id:
            raise ApiError(400, "invalid_request", "Team and member are required")
        return team_id, user_id

    @staticmethod
    def _package_target(path: str, suffix: str) -> tuple[str, str, str]:
        value = path[len("/admin/packages/"):-len(suffix)].strip("/")
        package_id, separator, version = value.rpartition("/")
        if not separator or not package_id or not version:
            raise ApiError(400, "invalid_request", "package_id and version are required")
        return package_id, version, f"{package_id}@{version}"
