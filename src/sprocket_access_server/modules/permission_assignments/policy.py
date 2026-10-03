from __future__ import annotations


def require_team_owner_assignment_change(
        *,
        owner_user_id: str | None,
        target_user_id: str,
        template_name: str,
) -> None:
    if owner_user_id == target_user_id and template_name.casefold() not in {"owner", "admin"}:
        raise ValueError("Team owner assignment cannot be downgraded")


def require_team_owner_removal(*, owner_user_id: str | None, target_user_id: str) -> None:
    if owner_user_id == target_user_id:
        raise ValueError("Team owner assignment cannot be removed")
