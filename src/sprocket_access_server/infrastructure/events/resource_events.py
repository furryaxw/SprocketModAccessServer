from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResourceChanged:
    kind: str
    node: str
    action: str
    data: dict[str, Any] = field(default_factory=dict)
    team_id: str | None = None
    user_id: str | None = None
