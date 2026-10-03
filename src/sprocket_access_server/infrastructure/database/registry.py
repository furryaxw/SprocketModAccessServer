from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SchemaContribution:
    module: str
    schema: str


class SchemaRegistry:
    """Collects module schema declarations before database initialization."""

    def __init__(self, database) -> None:
        self.database = database
        self._contributions: list[SchemaContribution] = []

    def register_schema(self, module: str, schema: str) -> None:
        if not module.strip() or not schema.strip():
            raise ValueError("schema contribution requires module and schema")
        self._contributions.append(SchemaContribution(module, schema))

    def initialize(self) -> None:
        self.database.initialize()
        for contribution in self._contributions:
            with self.database.transaction() as connection:
                connection.executescript(contribution.schema)

    def contributions(self) -> tuple[SchemaContribution, ...]:
        return tuple(self._contributions)
