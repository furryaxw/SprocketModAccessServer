from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


def _public_permission(node: str) -> str:
    return node


class KeyStatus(str, Enum):
    UNUSED = "unused"
    REDEEMED = "redeemed"
    REVOKED = "revoked"


class GrantStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class PermissionAssignment:
    assignment_id: str
    node: str
    effect: str = "allow"
    priority: int = 0
    grant: str = "allow"
    source_type: str = "direct"
    source_id: str = ""
    starts_at: int | None = None
    expires_at: int | None = None
    revoked_at: int | None = None

    def __post_init__(self) -> None:
        if self.effect not in {"allow", "deny"}:
            raise ValueError("assignment effect must be allow or deny")
        if self.grant not in {"allow", "deny"}:
            raise ValueError("assignment grant must be allow or deny")
        if self.priority < 0:
            raise ValueError("assignment priority must not be negative")

    def is_active(self, *, now: int) -> bool:
        return (
                self.revoked_at is None
                and (self.starts_at is None or self.starts_at <= now)
                and (self.expires_at is None or self.expires_at > now)
        )


@dataclass(frozen=True)
class PermissionDecision:
    node: str
    allowed: bool
    priority: int | None
    matched: tuple[PermissionAssignment, ...] = ()
    grantable: bool = False


@dataclass(frozen=True)
class UserGrant:
    grant_id: str
    github_user_id: str
    permissions: frozenset[str]
    expires_at: int | None
    status: GrantStatus = GrantStatus.ACTIVE
    source_type: str = "key"
    source_id: str = ""
    team_id: str | None = None
    created_by: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    template_id: str | None = None
    assignments: tuple[PermissionAssignment, ...] = ()

    def is_active(self, *, now: int) -> bool:
        return (
                self.status is GrantStatus.ACTIVE
                and (self.expires_at is None or self.expires_at > now)
        )


@dataclass(frozen=True)
class GrantRecord:
    grant_id: str
    github_user_id: str
    source_type: str
    source_id: str
    team_id: str | None
    status: GrantStatus
    starts_at: int | None
    expires_at: int | None
    revoked_at: int | None
    created_by: str
    created_at: int
    template_id: str | None = None
    assignments: tuple[PermissionAssignment, ...] = ()

    def is_active(self, *, now: int) -> bool:
        return (
                self.status is GrantStatus.ACTIVE
                and self.revoked_at is None
                and (self.starts_at is None or self.starts_at <= now)
                and (self.expires_at is None or self.expires_at > now)
        )

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset(
            _public_permission(item.node)
            for item in self.assignments
            if item.effect == "allow"
        )


@dataclass(frozen=True)
class ActivationKey:
    key_id: str
    key_hash: str
    permissions: frozenset[str]
    status: KeyStatus
    expires_at: int | None

    def redeem(self, *, now: int) -> "ActivationKey":
        if self.status is not KeyStatus.UNUSED:
            raise ValueError("activation key is unavailable")
        if self.expires_at is not None and self.expires_at <= now:
            raise ValueError("activation key has expired")
        return ActivationKey(self.key_id, self.key_hash, self.permissions, KeyStatus.REDEEMED, self.expires_at)

    def revoke(self) -> "ActivationKey":
        if self.status is not KeyStatus.UNUSED:
            raise ValueError("only unused activation keys can be revoked")
        return ActivationKey(self.key_id, self.key_hash, self.permissions, KeyStatus.REVOKED, self.expires_at)


def effective_permissions(grants: tuple[UserGrant | GrantRecord, ...], *, now: int) -> frozenset[str]:
    permissions: set[str] = set()
    for grant in grants:
        if grant.is_active(now=now):
            permissions.update(grant.permissions)
    return frozenset(permissions)
