from __future__ import annotations

import re
from collections.abc import Iterable

_CATALOG_SEGMENT = re.compile(r"^(?:[a-z0-9][a-z0-9_-]{0,63}|<[a-z][a-z0-9_]{0,63}>)$")

READ_ACTION = "read"
GRANT_ACTION = "grant"


def required_actions(actions: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for action in (READ_ACTION, *actions, GRANT_ACTION):
        normalized = action.strip().casefold()
        if not normalized:
            raise ValueError("permission action is required")
        if normalized not in ordered:
            ordered.append(normalized)
    return tuple(ordered)


def permission_node(path: str, action: str) -> str:
    return normalize_catalog_node(f"{path.strip('.')}.{action.strip('.')}")


def normalize_catalog_node(value: str) -> str:
    node = value.strip().casefold()
    if node == "*":
        return node
    parts = node.split(".")
    if not parts or any(not part for part in parts):
        raise ValueError(f"permission catalog node is invalid: {value}")
    if parts.count("*") > 1 or ("*" in parts[:-1]):
        raise ValueError(f"permission catalog wildcard must be terminal: {value}")
    if parts[-1] == "*":
        segments = parts[:-1]
    else:
        segments = parts
    if not segments or any(_CATALOG_SEGMENT.fullmatch(part) is None for part in segments):
        raise ValueError(f"permission catalog node is invalid: {value}")
    return ".".join(parts)


def generated_permission_nodes(nodes: Iterable[str]) -> tuple[str, ...]:
    normalized = sorted({normalize_catalog_node(node) for node in nodes})
    validate_permission_tree(normalized)
    return tuple(normalized)


def validate_permission_tree(nodes: Iterable[str]) -> None:
    for node in nodes:
        normalized = normalize_catalog_node(node)
        if normalized == "*" or "." in normalized:
            continue
        raise ValueError(f"permission node has no action: {node}")
