from __future__ import annotations

from .permissions import normalize_node, team_node

SYSTEM_TEAM_ID = "system"
TEMPLATE_TEAM_ID = "template"
DEFAULT_TEAM_ID = "default"
SYSTEM_ACCOUNT_ID = "system"


def split_package_id(package_id: str) -> tuple[str, str]:
    """`<team_id>.<mod>` 拆成 (team_id, mod)。mod 允许再含点，Team id 不含点。"""
    team_id, _, mod = package_id.partition(".")
    if not team_id or not mod:
        raise ValueError("package ID must be <team_id>.<mod>")
    return team_id, mod


def package_resource_node(team_id: str, package_id: str | None = None) -> str:
    if package_id is None:
        return team_node(team_id, "packages")
    owner_team_id, mod = split_package_id(package_id)
    if owner_team_id != team_id:
        raise ValueError("package ID must start with its Team id")
    package = normalize_node(mod.replace(".", "_"))
    if "." in package or package == "*":
        raise ValueError("package ID is invalid")
    return team_node(team_id, package)


def key_resource_node(team_id: str) -> str:
    return team_node(team_id, "keys")


def permission_assignment_resource_node(team_id: str) -> str:
    return team_node(team_id, "permission_assignments")


def permission_template_resource_node(team_id: str) -> str:
    return team_node(team_id, "permission_templates")
