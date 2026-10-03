from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.modules.permission_assignments.store import SQLiteAuthorizationStore
from sprocket_access_server.infrastructure.database import SQLiteDatabase


class AuthorizationStoreTests(unittest.TestCase):
    def store(self, directory: str) -> SQLiteAuthorizationStore:
        return SQLiteAuthorizationStore(SQLiteDatabase(Path(directory) / "access.db"))

    def test_redeem_is_one_time_and_permissions_are_scoped(self) -> None:
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            store.issue_key(
                key_id="key-1", key_hash="hash-1",
                permissions=frozenset({"team.default.packages.mod.download"}), expires_at=200, now=100,
            )
            grant = store.redeem_key(key_hash="hash-1", github_user_id="123", now=150)
            self.assertEqual(store.permissions("123", now=150), frozenset({"team.default.packages.mod.download"}))
            self.assertEqual(grant.grant_id, store.grants("123")[0].grant_id)
            with store.database.transaction() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM grants").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM grant_assignments").fetchone()[0], 1)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                store.redeem_key(key_hash="hash-1", github_user_id="456", now=150)
            self.assertEqual(store.permissions("456", now=150), frozenset())

    def test_expired_key_is_rejected_without_creating_user_or_grant(self) -> None:
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            store.issue_key(key_id="key-1", key_hash="hash-1", permissions=frozenset({"p"}), expires_at=100, now=1)
            with self.assertRaisesRegex(ValueError, "expired"):
                store.redeem_key(key_hash="hash-1", github_user_id="123", now=100)
            self.assertEqual(store.grants("123"), ())

    def test_revoke_removes_only_one_grant_permission(self) -> None:
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            for index in (1, 2):
                store.issue_key(
                    key_id=f"key-{index}", key_hash=f"hash-{index}",
                    permissions=frozenset({"team.default.packages.mod.download"}), expires_at=None, now=1,
                )
                store.redeem_key(key_hash=f"hash-{index}", github_user_id="123", now=2)
            grants = store.grants("123")
            store.revoke_grant(grants[0].grant_id, now=3)
            self.assertEqual(store.permissions("123", now=3), frozenset({"team.default.packages.mod.download"}))
            store.revoke_grant(grants[1].grant_id, now=4)
            self.assertEqual(store.permissions("123", now=4), frozenset())

    def test_concurrent_redeem_allows_one_winner(self) -> None:
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            store.issue_key(
                key_id="key-1", key_hash="hash-1",
                permissions=frozenset({"team.default.packages.mod.download"}), expires_at=None, now=1,
            )

            def redeem() -> bool:
                try:
                    store.redeem_key(key_hash="hash-1", github_user_id="123", now=2)
                    return True
                except ValueError:
                    return False

            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda _: redeem(), range(16)))
            self.assertEqual(sum(results), 1)
            self.assertEqual(len(store.grants("123")), 1)

    def test_redeem_requires_matching_team_and_grant_inherits_team(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            store = SQLiteAuthorizationStore(database)
            store.issue_key(key_id="key-1", key_hash="hash-1", permissions=frozenset({"team.default.packages.mod.download"}),
                            expires_at=None, now=1)
            with database.transaction() as connection:
                connection.execute("UPDATE activation_keys SET team_id='team-a' WHERE key_id='key-1'")
            with self.assertRaisesRegex(ValueError, "unavailable"):
                store.redeem_key(key_hash="hash-1", github_user_id="123", team_id="team-b", now=2)
            store.redeem_key(key_hash="hash-1", github_user_id="123", team_id="team-a", now=2)
            self.assertEqual(len(store.grants("123", team_id="team-a")), 1)
            self.assertEqual(store.grants("123", team_id="team-b"), ())

    def test_allows_applies_assignment_wildcards_and_priority_denies(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            store = SQLiteAuthorizationStore(database)
            store.issue_key(
                key_id="key-1", key_hash="hash-1", permissions=frozenset({"team.default.packages.mod.download"}),
                expires_at=None, now=1,
            )
            grant = store.redeem_key(key_hash="hash-1", github_user_id="123", now=2)
            with database.transaction() as connection:
                connection.execute(
                    """INSERT INTO grant_assignments
                       (assignment_id,grant_id,node,effect,priority,grant_effect,source_type,source_id)
                       VALUES('deny',?,'team.default.packages.mod.download','deny',10,'allow','test','deny')""",
                    (grant.grant_id,),
                )
            self.assertFalse(store.allows("123", "team.default.packages.mod.download", now=3))
            self.assertEqual(store.permissions("123", now=3), frozenset())

    def test_assignment_search_matches_user_exactly_unless_asked_otherwise(self) -> None:
        """按 id 精确查找不能被前缀相同的 id 连带命中。"""
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            for key_id, key_hash, user_id in (("key-1", "hash-1", "123"), ("key-2", "hash-2", "9123456")):
                store.issue_key(key_id=key_id, key_hash=key_hash, team_id="default",
                                permissions=frozenset({"team.default.packages.mod.download"}),
                                expires_at=500, now=1)
                store.redeem_key(key_hash=key_hash, github_user_id=user_id, now=2)

            exact, exact_total = store.search_assignments(user_id="123", team_id="default", now=3)
            self.assertEqual(exact_total, 1)
            self.assertEqual({row["github_user_id"] for row in exact}, {"123"})

            fuzzy, fuzzy_total = store.search_assignments(user_id_like="123", team_id="default", now=3)
            self.assertEqual(fuzzy_total, 2)
            self.assertEqual({row["github_user_id"] for row in fuzzy}, {"123", "9123456"})

            exact_node, exact_node_total = store.search_assignments(
                node="team.default.packages.mod.download", team_id="default", now=3,
            )
            self.assertEqual(exact_node_total, 2)
            self.assertEqual(len(exact_node), 2)

            # 子串本身不是节点名：等值过滤不会命中它。
            fragment, fragment_total = store.search_assignments(
                node="packages.mod", team_id="default", now=3,
            )
            self.assertEqual((fragment, fragment_total), ([], 0))

            like_node, like_total = store.search_assignments(
                node_like="packages.mod", team_id="default", now=3,
            )
            self.assertEqual(like_total, 2)
            self.assertEqual(len(like_node), 2)

    def test_grant_update_changes_permissions_and_expiry(self) -> None:
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            store.issue_key(key_id="key-1", key_hash="hash-1", permissions=frozenset({"team.default.packages.old.download"}),
                            expires_at=500, now=1)
            grant = store.redeem_key(key_hash="hash-1", github_user_id="123", now=2)
            preview = store.preview_grant_update(grant.grant_id, permissions={"team.default.packages.new.download": "allow"},
                                                 expires_at=800, expires_at_provided=True)
            self.assertEqual(preview["diff"]["permissions_removed"], ["team.default.packages.old.download"])
            self.assertTrue(preview["diff"]["expiry_shortened"] is False)
            updated = store.update_grant(grant.grant_id, permissions={"team.default.packages.new.download": "allow"},
                                         expires_at=800, expires_at_provided=True, now=10)
            self.assertEqual(updated.permissions, frozenset({"team.default.packages.new.download"}))
            self.assertEqual(updated.expires_at, 800)

    def test_revoked_grant_cannot_be_restored(self) -> None:
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            store.issue_key(key_id="key-1", key_hash="hash-1", permissions=frozenset({"team.default.packages.old.download"}),
                            expires_at=None, now=1)
            grant = store.redeem_key(key_hash="hash-1", github_user_id="123", now=2)
            store.revoke_grant(grant.grant_id, now=3)
            with self.assertRaisesRegex(ValueError, "revoked"):
                store.update_grant(grant.grant_id, permissions=frozenset({"team.default.packages.new.download"}), now=4)

    def test_grant_update_is_team_scoped(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            store = SQLiteAuthorizationStore(database)
            store.issue_key(key_id="key-1", key_hash="hash-1", permissions=frozenset({"team.default.packages.old.download"}),
                            expires_at=None, now=1)
            with database.transaction() as connection:
                connection.execute("UPDATE activation_keys SET team_id='team-a' WHERE key_id='key-1'")
            grant = store.redeem_key(key_hash="hash-1", github_user_id="123", team_id="team-a", now=2)
            with self.assertRaisesRegex(ValueError, "not found"):
                store.update_grant(grant.grant_id, permissions=frozenset({"team.default.packages.new.download"}), team_id="team-b", now=3)


if __name__ == "__main__":
    unittest.main()
