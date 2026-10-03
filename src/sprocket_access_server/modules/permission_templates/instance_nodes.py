"""权限模板实例节点。

模板是**可分配对象**，在后端树里用节点表示：

```text
team.<team_id>.templates.<template-id>         模板本身：拿到该节点就是拿到模板的权限集
team.<team_id>.templates.<template-id>.grant   允许把该模板分配给其他人
```

节点末段用 `template_id`（非法字符替换成 `-`，因为节点段只允许 `[a-z0-9_-]`）；模板停用后
节点从目录消失。授权里出现模板实例节点时，求值阶段展开成模板的成员节点 —— 这样前端只需
渲染后端给的目录，不需要自己拼接。

命名空间刻意用 `templates` 而不是 `permission_templates`：后者是**模板管理页自身的权限
资源**（谁可以看/建/改/停用模板），与"某个模板本身"是两件事。
"""

from __future__ import annotations

import re

from ...infrastructure.database import SQLiteDatabase

TEMPLATE_RESOURCE = "templates"

_INVALID = re.compile(r"[^a-z0-9_-]+")
_MULTI_DASH = re.compile(r"-{2,}")
_SEGMENT_MAX = 64


def template_node_segment(template_id: str) -> str:
    """把 template_id 变成合法的节点段（`normalize_node` 的段规则）。"""
    value = _INVALID.sub("-", template_id.strip().casefold())
    value = _MULTI_DASH.sub("-", value).strip("-")
    if not value:
        value = "template"
    if not value[0].isalnum():
        value = f"t{value}"
    value = value[:_SEGMENT_MAX].strip("-")
    return value or "template"


def instance_node_map(connection, team_id: str) -> dict[str, str]:
    """team 内 active 模板的 `节点 -> template_id` 映射。

    同一 team 内节点段重名时按 template_id 顺序追加 `-2`、`-3` 去重，保持确定性。
    """
    rows = connection.execute(
        "SELECT template_id FROM permission_templates WHERE team_id = ? AND status = 'active' "
        "ORDER BY template_id",
        (team_id,),
    ).fetchall()
    used: set[str] = set()
    result: dict[str, str] = {}
    for row in rows:
        template_id = str(row["template_id"])
        base = template_node_segment(template_id)
        segment = base
        index = 2
        while segment in used:
            segment = f"{base}-{index}"
            index += 1
        used.add(segment)
        result[f"team.{team_id}.{TEMPLATE_RESOURCE}.{segment}"] = template_id
    return result


def template_instance_node(connection, team_id: str, template_name: str) -> str:
    """按模板 id 或名字取它的实例节点。

    授权只记这个节点：模板内容变化在求值阶段展开，因此改模板即对所有引用者生效，
    不需要在分配时把节点展开成一堆快照。
    """
    wanted = template_name.strip()
    row = connection.execute(
        "SELECT template_id FROM permission_templates WHERE team_id = ? AND status = 'active' "
        "AND (template_id = ? OR lower(name) = lower(?)) ORDER BY template_id LIMIT 1",
        (team_id, wanted, wanted),
    ).fetchone()
    if row is None:
        raise ValueError("permission template was not found")
    template_id = str(row["template_id"])
    for node, candidate in instance_node_map(connection, team_id).items():
        if candidate == template_id:
            return node
    raise ValueError("permission template is not assignable")


def all_instance_nodes(database: SQLiteDatabase) -> tuple[str, ...]:
    """全部 Team 的模板实例节点（用于并进权限目录）。"""
    with database.transaction() as connection:
        team_ids = _instance_team_ids(connection)
        nodes: list[str] = []
        for team_id in team_ids:
            nodes.extend(instance_node_map(connection, team_id))
    return tuple(sorted(nodes))


def _instance_team_ids(connection) -> tuple[str, ...]:
    return tuple(
        str(row["team_id"])
        for row in connection.execute(
            "SELECT DISTINCT team_id FROM permission_templates WHERE status = 'active' ORDER BY team_id"
        ).fetchall()
    )


def is_template_node(node: str) -> bool:
    parts = node.split(".")
    return len(parts) == 4 and parts[0] == "team" and parts[2] == TEMPLATE_RESOURCE


def template_id_for_node(connection, node: str) -> str | None:
    """节点 -> template_id；只认当前 active 的模板。"""
    parts = node.split(".")
    if len(parts) != 4 or parts[0] != "team" or parts[2] != TEMPLATE_RESOURCE:
        return None
    return instance_node_map(connection, parts[1]).get(node)
