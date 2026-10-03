from __future__ import annotations

import re

_NON_ID = re.compile(r"[^a-z0-9]+")


def derive_team_id(value: str) -> str:
    """显示名到 team id：小写、非字母数字折叠成连字符，空结果回退为 `team`。

    Team id 会进入权限节点的 `team.<id>.` 段，节点按 `.` 分段，因此 id 中不能出现点。
    """
    return _NON_ID.sub("-", value.strip().casefold()).strip("-") or "team"


def unique_team_id(taken: set[str], base: str) -> str:
    """在已占用的 id 集合里给出唯一值。"""
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"
