from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from sprocket_access_server.infrastructure.security.github import GitHubApiIdentityProvider


class FakeResponse:
    def __init__(self, payload: object):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload


class GitHubProviderTests(unittest.TestCase):
    def test_provider_returns_numeric_identity(self) -> None:
        with patch("sprocket_access_server.infrastructure.security.github.urlopen",
                   return_value=FakeResponse({"id": 123, "login": "user"})):
            identity = GitHubApiIdentityProvider().verify_access_token("token")
        self.assertEqual(identity.user_id, "123")
        self.assertEqual(identity.login, "user")

    def test_provider_exposes_upstream_auth_outcome_without_body(self) -> None:
        error = HTTPError("https://api.github.com/user", 401, "unauthorized", {}, io.BytesIO(b"secret"))
        with patch("sprocket_access_server.infrastructure.security.github.urlopen", side_effect=error):
            with self.assertRaisesRegex(ValueError, "rejected") as raised:
                GitHubApiIdentityProvider().verify_access_token("token")
        self.assertNotIn("secret", str(raised.exception))
        self.assertEqual(raised.exception.status, 401)


if __name__ == "__main__":
    unittest.main()
