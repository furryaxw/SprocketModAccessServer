from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_SEED_DIR = Path(__file__).with_name("seed")


def load_template_seed(name: str) -> list[dict[str, Any]]:
    path = _SEED_DIR / f"{name}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"permission template seed is invalid: {path}") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError(f"permission template seed must be a list of objects: {path}")
    return value
