from __future__ import annotations

import unittest

from sprocket_access_server.domain.models import (
    ActivationKey,
    GrantStatus,
    KeyStatus,
    PermissionAssignment,
    UserGrant,
    effective_permissions,
)
from sprocket_access_server.domain.permissions import evaluate, permission_to_node


class DomainTests(unittest.TestCase):
    def test_key_can_only_move_from_unused_to_redeemed_once(self) -> None:
        key = ActivationKey("k", "h", frozenset({"team.default.packages.a.download"}), KeyStatus.UNUSED, 200)
        redeemed = key.redeem(now=100)
        self.assertEqual(redeemed.status, KeyStatus.REDEEMED)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            redeemed.redeem(now=100)

    def test_expired_key_and_redeemed_key_cannot_be_revoked(self) -> None:
        expired = ActivationKey("k", "h", frozenset(), KeyStatus.UNUSED, 100)
        with self.assertRaisesRegex(ValueError, "expired"):
            expired.redeem(now=100)
        redeemed = ActivationKey("k", "h", frozenset(), KeyStatus.REDEEMED, None)
        with self.assertRaisesRegex(ValueError, "only unused"):
            redeemed.revoke()

    def test_permissions_union_only_active_unexpired_grants(self) -> None:
        grants = (
            UserGrant("a", "123", frozenset({"team.default.packages.a.download"}), 200),
            UserGrant("b", "123", frozenset({"team.default.packages.b.download"}), 100),
            UserGrant("c", "123", frozenset({"team.default.packages.c.download"}), None, GrantStatus.REVOKED),
        )
        self.assertEqual(effective_permissions(grants, now=150), frozenset({"team.default.packages.a.download"}))

    def test_permission_evaluator_uses_priority_and_denies_equal_priority(self) -> None:
        assignments = (
            PermissionAssignment("allow", permission_to_node("team.default.packages.mod.download"), priority=10),
            PermissionAssignment("deny", permission_to_node("team.default.packages.mod.download"), effect="deny", priority=10),
            PermissionAssignment("wildcard", "download.*", priority=1),
        )
        decision = evaluate(assignments, permission_to_node("team.default.packages.mod.download"), now=1)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.priority, 10)
        self.assertEqual({item.assignment_id for item in decision.matched}, {"allow", "deny"})


if __name__ == "__main__":
    unittest.main()
