from __future__ import annotations

import re
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

_ACTION = re.compile(r"^[a-z][a-z0-9_:-]{0,63}$")
_NODE_SEGMENT = re.compile(r"^(?:[a-z0-9][a-z0-9_-]{0,63}|\*)$")
_READ = "read"
_GRANT = "grant"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResourceOperation:
    action: str
    handler: Callable[..., Any]
    requires_permission: bool = True


@dataclass(frozen=True)
class ResourceDefinition:
    node: str
    description: str = ""
    operations: tuple[ResourceOperation, ...] = ()
    auto_grant: bool = False


@dataclass(frozen=True)
class ResourceMatch:
    definition: ResourceDefinition
    operation: ResourceOperation


class ResourceRegistration:
    def __init__(self, registry: "ResourceRegistry", node: str) -> None:
        self._registry = registry
        self._node = self._registry._normalize_node(node)

    @property
    def node(self) -> str:
        return self._node

    def add_perm(self, action: str, handler: Callable[..., Any]) -> "ResourceRegistration":
        normalized_action = self._registry._normalize_action(action)
        self._registry._append_operation(self._node, ResourceOperation(normalized_action, handler))
        return self

    def add_public(self, action: str, handler: Callable[..., Any]) -> "ResourceRegistration":
        normalized_action = self._registry._normalize_action(action)
        self._registry._append_operation(self._node, ResourceOperation(normalized_action, handler, False))
        return self


class ResourceRegistry:
    """Resource-first operation registry owned by infrastructure."""

    def __init__(self) -> None:
        self._resources: dict[str, ResourceDefinition] = {}
        self._module_scope_depth = 0

    @contextmanager
    def module_scope(self):
        self._module_scope_depth += 1
        try:
            yield self
        finally:
            self._module_scope_depth -= 1

    def register(self, node: str, *, description: str = "", auto_grant: bool | None = None) -> ResourceRegistration:
        normalized_node = self._normalize_node(node)
        generated = self._module_scope_depth > 0 if auto_grant is None else auto_grant
        if normalized_node not in self._resources:
            self._resources[normalized_node] = ResourceDefinition(
                normalized_node, description.strip(), (), generated
            )
        elif description.strip() and not self._resources[normalized_node].description:
            definition = self._resources[normalized_node]
            self._resources[normalized_node] = ResourceDefinition(
                definition.node,
                description.strip(),
                definition.operations,
                definition.auto_grant or generated,
            )
        elif generated and not self._resources[normalized_node].auto_grant:
            definition = self._resources[normalized_node]
            self._resources[normalized_node] = ResourceDefinition(
                definition.node,
                definition.description,
                definition.operations,
                True,
            )
        return ResourceRegistration(self, normalized_node)

    def add(self, action: str, node: str, handler: Callable[..., Any], *,
            description: str = "", auto_grant: bool | None = None) -> ResourceRegistration:
        return self.register(
            node,
            description=description,
            auto_grant=auto_grant,
        ).add_perm(action, handler)

    def definitions(self) -> tuple[ResourceDefinition, ...]:
        return tuple(self._resources.values())

    def unregister(self, node: str) -> None:
        """注销一个节点。

        只有随数据消失的节点才会走这里（例如整包删除后的 `team.<team>.<mod>`）；固定资源
        （system/team 那一层）永不注销，所以目录不会因为一次删除而少掉它们。
        """
        self._resources.pop(self._normalize_node(node), None)

    def resolve(self, action: str, node: str) -> ResourceMatch | None:
        normalized_action = self._normalize_action(action)
        normalized_node = self._normalize_node(node)
        definition = self._resources.get(normalized_node)
        if definition is not None:
            for operation in definition.operations:
                if operation.action == normalized_action:
                    return ResourceMatch(definition, operation)
        return None

    def dispatch(
            self,
            action: str,
            node: str,
            *,
            data: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            now: int | None = None,
    ) -> Any:
        match = self.resolve(action, node)
        if match is None:
            logger.warning("resource operation not found action=%s node=%s", action, node)
            raise KeyError(f"resource operation is not registered: {action} {node}")
        logger.debug("resource operation resolved action=%s node=%s requires_permission=%s",
                     action, node, match.operation.requires_permission)
        return match.operation.handler(data or {}, headers or {}, now)

    def permission_nodes(self) -> tuple[str, ...]:
        nodes: set[str] = set()
        for definition in self._resources.values():
            actions = {
                operation.action
                for operation in definition.operations
                if operation.requires_permission
            }
            if definition.auto_grant:
                actions.add(_GRANT)
            nodes.update(f"{definition.node}.{action}" for action in actions)
        return tuple(sorted(nodes))

    def _append_operation(self, node: str, operation: ResourceOperation) -> None:
        definition = self._resources[node]
        if any(existing.action == operation.action for existing in definition.operations):
            raise ValueError(f"duplicate resource operation: {operation.action} {node}")
        self._resources[node] = ResourceDefinition(
            node=definition.node,
            description=definition.description,
            operations=definition.operations + (operation,),
            auto_grant=definition.auto_grant,
        )

    @staticmethod
    def _normalize_action(value: str) -> str:
        action = value.strip().casefold()
        if not action or _ACTION.fullmatch(action) is None:
            raise ValueError(f"resource action is invalid: {value}")
        return action

    @staticmethod
    def _normalize_node(value: str) -> str:
        node = value.strip().casefold()
        if node == "*":
            return node
        parts = node.split(".")
        if not parts or any(not part for part in parts):
            raise ValueError(f"resource node is invalid: {value}")
        if parts.count("*") > 1 or ("*" in parts[:-1]):
            raise ValueError(f"resource wildcard must be terminal: {value}")
        if not all(_NODE_SEGMENT.fullmatch(part) is not None for part in parts):
            raise ValueError(f"resource node is invalid: {value}")
        if any(part.startswith("<") or part.endswith(">") for part in parts):
            raise ValueError(f"resource node is invalid: {value}")
        return node
