from __future__ import annotations

import unittest

from sprocket_access_server.core.ports import GitHubIdentity


class PortContractTests(unittest.TestCase):
    def test_github_identity_uses_numeric_id_as_stable_key(self) -> None:
        identity = GitHubIdentity(user_id="123", login="renamed-user")
        self.assertEqual(identity.user_id, "123")
        self.assertEqual(identity.login, "renamed-user")


if __name__ == "__main__":
    unittest.main()
