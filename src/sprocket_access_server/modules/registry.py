from __future__ import annotations

from . import DEFAULT_MODULES
from ..core.contracts import BusinessModule


def registered_modules() -> tuple[BusinessModule, ...]:
    return DEFAULT_MODULES
