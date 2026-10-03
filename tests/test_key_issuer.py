from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_access_server.modules.permission_assignments.store import SQLiteAuthorizationStore
from sprocket_access_server.infrastructure.database import SQLiteDatabase
from sprocket_access_server.modules.keys.store import KeyIssuer


class KeyIssuerTests(unittest.TestCase):
    def test_batch_stores_readable_plaintext_and_matching_hash(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            issuer = KeyIssuer(database, SQLiteAuthorizationStore(database), b"pepper")
            keys = issuer.create_batch(
                quantity=3, permissions=frozenset({"team.default.packages.mod.download"}), expires_at=200,
                actor="admin:123", now=100,
            )
            self.assertEqual(len(keys), 3)
            for item in keys:
                self.assertRegex(item.plaintext, r"^SMAS[0-9A-HJKMNP-TV-Z]{20}$")
                self.assertNotRegex(item.plaintext, r"[-_]")
            with database.transaction() as connection:
                rows = connection.execute("SELECT key_id, key_hash, key_plaintext FROM activation_keys").fetchall()
            stored = {row["key_id"]: row for row in rows}
            for item in keys:
                # 明文随库存保存，且仍与用于兑换的哈希一致。
                self.assertEqual(stored[item.key_id]["key_plaintext"], item.plaintext)
                self.assertEqual(stored[item.key_id]["key_hash"], issuer.hash_key(item.plaintext))

    def test_batch_limits_are_enforced(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            issuer = KeyIssuer(database, SQLiteAuthorizationStore(database), b"pepper")
            with self.assertRaisesRegex(ValueError, "invalid"):
                issuer.create_batch(quantity=0, permissions=frozenset({"p"}), expires_at=None, actor="admin")

    def test_taking_a_batch_marks_delivery_and_can_be_released(self) -> None:
        """取走 N 枚会记下发给谁；未兑换的发放可以撤销。"""
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            issuer = KeyIssuer(database, SQLiteAuthorizationStore(database), b"pepper")
            issued = issuer.create_batch(quantity=2, permissions=frozenset({"team.default.packages.mod.download"}),
                                         expires_at=None, actor="admin", team_id="team-a", now=100)
            batch_id = issued[0].batch_id

            taken = issuer.take_batch(batch_id=batch_id, count=1, recipient="discord:42", actor="admin",
                                      team_id="team-a", now=110)
            self.assertEqual(len(taken), 1)
            self.assertEqual(taken[0].delivered_to, "discord:42")
            self.assertEqual(taken[0].delivered_at, 110)
            self.assertEqual(taken[0].batch_id, batch_id)
            self.assertIn(taken[0].plaintext, {item.plaintext for item in issued})

            with self.assertRaisesRegex(ValueError, "does not hold that many"):
                issuer.take_batch(batch_id=batch_id, count=2, recipient="discord:43", actor="admin",
                                  team_id="team-a", now=111)

            delivered, _, delivered_total = issuer.search(batch_id=batch_id, delivered=True, team_id="team-a")
            pending, _, pending_total = issuer.search(batch_id=batch_id, delivered=False, team_id="team-a")
            self.assertEqual((delivered_total, pending_total), (1, 1))
            self.assertEqual(pending[0].delivered_to, None)
            self.assertEqual(delivered[0].key_id, taken[0].key_id)

            issuer.release_key(taken[0].key_id, actor="admin", team_id="team-a", now=120)
            _, _, released_total = issuer.search(batch_id=batch_id, delivered=False, team_id="team-a")
            self.assertEqual(released_total, 2)
            with self.assertRaisesRegex(ValueError, "no delivered unused key"):
                issuer.release_key(taken[0].key_id, actor="admin", team_id="team-a", now=121)

    def test_inventory_filters_by_batch_and_reports_the_batch(self) -> None:
        """按批次取未使用的 Key：批次号可过滤，每行带回自己的批次号。"""
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            issuer = KeyIssuer(database, SQLiteAuthorizationStore(database), b"pepper")
            first = issuer.create_batch(quantity=2, permissions=frozenset({"team.default.packages.mod.download"}),
                                        expires_at=None, actor="admin", team_id="team-a", now=100)
            second = issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.mod.download"}),
                                         expires_at=None, actor="admin", team_id="team-a", now=101)
            batch_id = first[0].batch_id
            self.assertTrue(batch_id)
            self.assertNotEqual(batch_id, second[0].batch_id)

            rows, counts, total = issuer.search(batch_id=batch_id, status="unused", team_id="team-a")
            other, _, other_total = issuer.search(batch_id=second[0].batch_id, team_id="team-a")

        self.assertEqual(total, 2)
        self.assertEqual(counts, {"unused": 2})
        self.assertEqual({row.key_id for row in rows}, {item.key_id for item in first})
        self.assertEqual({row.batch_id for row in rows}, {batch_id})
        self.assertEqual(other_total, 1)
        self.assertEqual(other[0].batch_id, second[0].batch_id)

    def test_inventory_exposes_prefix_plaintext_and_editable_fields(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            issuer = KeyIssuer(database, SQLiteAuthorizationStore(database), b"pepper")
            issued = issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.mod.download"}), expires_at=200,
                                         actor="admin", team_id="team-a", now=100)[0]
            issuer.update(issued.key_id, actor="admin", note="QA", permissions=frozenset({"team.default.packages.mod.download", "team.default.packages.mod.read"}),
                          team_id="team-a", now=101)
            rows, _, _ = issuer.search(team_id="team-a")
        self.assertEqual(rows[0].key_prefix, issued.plaintext[:10])
        self.assertEqual(rows[0].note, "QA")
        self.assertEqual(rows[0].permissions, ("team.default.packages.mod.download", "team.default.packages.mod.read"))
        self.assertEqual(rows[0].plaintext, issued.plaintext)

    def test_redeemed_key_update_synchronizes_grant_and_audit_atomically(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authorization = SQLiteAuthorizationStore(database)
            issuer = KeyIssuer(database, authorization, b"pepper")
            issued = issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.mod.download", "team.default.packages.mod.read"}),
                                         expires_at=500, actor="admin", now=100)[0]
            grant = authorization.redeem_key(key_hash=issuer.hash_key(issued.plaintext), github_user_id="42", now=110)
            result = issuer.update(issued.key_id, actor="admin", permissions=frozenset({"team.default.packages.mod.download"}),
                                   expires_at=400, expires_at_provided=True, now=120)
            with database.transaction() as connection:
                row = connection.execute("SELECT * FROM grants WHERE grant_id=?", (grant.grant_id,)).fetchone()
                assignments = connection.execute(
                    "SELECT node FROM grant_assignments WHERE grant_id=? ORDER BY node",
                    (grant.grant_id,),
                ).fetchall()
                events = connection.execute("SELECT action, team_id FROM audit_events ORDER BY id").fetchall()
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual([item["node"] for item in assignments], ["team.default.packages.mod.download"])
        self.assertEqual(row["expires_at"], 400)
        self.assertEqual([item["action"] for item in events], ["grant.sync_from_key", "key.update"])

    def test_revoking_key_revokes_grant_and_cannot_be_restored(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            authorization = SQLiteAuthorizationStore(database)
            issuer = KeyIssuer(database, authorization, b"pepper")
            issued = issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.mod.download"}), expires_at=None,
                                         actor="admin", now=100)[0]
            grant = authorization.redeem_key(key_hash=issuer.hash_key(issued.plaintext), github_user_id="42", now=110)
            issuer.update(issued.key_id, actor="admin", status="revoked", now=120)
            with self.assertRaisesRegex(ValueError, "cannot be restored"):
                issuer.update(issued.key_id, actor="admin", status="redeemed", now=130)
            with database.transaction() as connection:
                grant_status = connection.execute("SELECT status FROM grants WHERE grant_id=?",
                                                  (grant.grant_id,)).fetchone()[0]
        self.assertEqual(grant_status, "revoked")

    def test_key_update_is_team_scoped(self) -> None:
        with TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "access.db")
            issuer = KeyIssuer(database, SQLiteAuthorizationStore(database), b"pepper")
            issued = issuer.create_batch(quantity=1, permissions=frozenset({"team.default.packages.mod.download"}), expires_at=None,
                                         actor="admin", team_id="team-a", now=100)[0]
            with self.assertRaisesRegex(ValueError, "not found"):
                issuer.update(issued.key_id, actor="admin", note="leak", team_id="team-b", now=101)


if __name__ == "__main__":
    unittest.main()
