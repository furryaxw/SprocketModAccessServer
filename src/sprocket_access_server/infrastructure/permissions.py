from __future__ import annotations

from dataclasses import dataclass

from .database import SQLiteDatabase
from ..domain.permission_tree import validate_permission_tree
from ..domain.permissions import normalize_node


@dataclass(frozen=True)
class PermissionEntry:
    value: str
    namespace: str
    source: str
    label: str


class PermissionCatalog:
    def __init__(self, database: SQLiteDatabase, *, resource_permissions=()):
        self.database = database
        self.permission_nodes = tuple(sorted(resource_permissions))
        validate_permission_tree(self.permission_nodes)
        self.database.initialize()

    @classmethod
    def validate(cls, permissions: list[str] | tuple[str, ...] | set[str] | frozenset[str]) -> frozenset[str]:
        if not permissions:
            raise ValueError("at least one permission is required")
        normalized: set[str] = set()
        for raw in permissions:
            if not isinstance(raw, str):
                raise ValueError("permission must be a string")
            try:
                value = normalize_node(raw)
            except ValueError as exc:
                raise ValueError(f"permission format is invalid: {raw}")
            normalized.add(value)
        return frozenset(normalized)

    def list(self, *, team_id: str | None = None, package_store=None, template_store=None) -> tuple[
        PermissionEntry, ...]:
        values: dict[str, str] = {}
        for node in self.permission_nodes:
            values.setdefault(node, "generated")
        if template_store is not None:
            for template in template_store.list(team_id=team_id):
                for permission in template.permissions:
                    values.setdefault(permission, "template")
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT node FROM permission_nodes WHERE active=1 ORDER BY node",
            ).fetchall()
            for row in rows:
                values.setdefault(row["node"], "stored")
        return tuple(PermissionEntry(value, _catalog_namespace(value), source, value)
                     for value, source in sorted(values.items()))


def _catalog_namespace(value: str) -> str:
    if "." in value:
        return value.split(".", 1)[0]
    return value
