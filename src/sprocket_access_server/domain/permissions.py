from __future__ import annotations

import re
from collections.abc import Iterable

from .models import PermissionAssignment, PermissionDecision

_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def normalize_node(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("permission node must be a string")
    node = value.strip().casefold()
    if node == "*":
        return node
    parts = node.split(".")
    if not parts or any(not part for part in parts):
        raise ValueError(f"permission node is invalid: {value}")
    if parts.count("*") > 1 or ("*" in parts[:-1]):
        raise ValueError(f"permission node wildcard must be terminal: {value}")
    if parts[-1] != "*" and any(not _SEGMENT.fullmatch(part) for part in parts):
        raise ValueError(f"permission node is invalid: {value}")
    if parts[-1] == "*" and (len(parts) == 1 or any(not _SEGMENT.fullmatch(part) for part in parts[:-1])):
        raise ValueError(f"permission node is invalid: {value}")
    return ".".join(parts)


def normalize_nodes(values: Iterable[str]) -> tuple[str, ...]:
    normalized = sorted({normalize_node(value) for value in values})
    if not normalized:
        raise ValueError("at least one permission node is required")
    return tuple(normalized)


def permission_to_node(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("permission must be a string")
    permission = value.strip().casefold()
    return normalize_node(permission)


def node_to_permission(value: str) -> str:
    return normalize_node(value)


def matches(pattern: str, requested: str) -> bool:
    pattern = normalize_node(pattern)
    requested = normalize_node(requested)
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return requested.startswith(pattern[:-2] + ".")
    return pattern == requested


def team_node(team_id: str, suffix: str) -> str:
    team = normalize_node(team_id)
    if "." in team or team == "*":
        raise ValueError("Team ID is invalid")
    tail = suffix.strip(".")
    if not tail:
        raise ValueError("Team permission suffix is required")
    return normalize_node(f"team.{team}.{tail}")


def team_scope(node: str) -> str | None:
    parts = normalize_node(node).split(".")
    if len(parts) >= 3 and parts[0] == "team" and parts[1] != "*":
        return parts[1]
    return None


def evaluate(assignments: Iterable[PermissionAssignment], requested: str, *, now: int) -> PermissionDecision:
    requested = normalize_node(requested)
    matched = tuple(
        assignment
        for assignment in assignments
        if assignment.is_active(now=now) and matches(assignment.node, requested)
    )
    if not matched:
        return PermissionDecision(requested, False, None, ())
    priority = max(item.priority for item in matched)
    highest = tuple(item for item in matched if item.priority == priority)
    denied = any(item.effect == "deny" for item in highest)
    allowed = not denied and any(item.effect == "allow" for item in highest)
    grantable = allowed and not any(item.grant == "deny" for item in highest)
    return PermissionDecision(requested, allowed, priority, highest, grantable)
