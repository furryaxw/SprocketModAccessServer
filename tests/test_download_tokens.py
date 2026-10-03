from __future__ import annotations

import unittest

from sprocket_access_server.modules.packages.download_tokens import DownloadTokenCodec


class DownloadTokenTests(unittest.TestCase):
    def test_token_is_bound_to_claims_and_expires(self) -> None:
        codec = DownloadTokenCodec(b"download-secret", max_ttl=60)
        token = codec.issue(github_user_id="123", package_id="mod.a", version="1.0.0", now=100, ttl=30)
        claim = codec.verify(token, github_user_id="123", package_id="mod.a", version="1.0.0", now=129)
        self.assertEqual(claim.expires_at, 130)
        with self.assertRaisesRegex(ValueError, "expired"):
            codec.verify(token, github_user_id="123", package_id="mod.a", version="1.0.0", now=130)

    def test_tampering_and_cross_user_replay_are_rejected(self) -> None:
        codec = DownloadTokenCodec(b"download-secret")
        token = codec.issue(github_user_id="123", package_id="mod.a", version="1.0.0", now=100)
        tampered = ("A" if token[0] != "A" else "B") + token[1:]
        with self.assertRaisesRegex(ValueError, "invalid"):
            codec.verify(tampered, github_user_id="123", package_id="mod.a", version="1.0.0", now=101)
        with self.assertRaisesRegex(ValueError, "does not match"):
            codec.verify(token, github_user_id="456", package_id="mod.a", version="1.0.0", now=101)


if __name__ == "__main__":
    unittest.main()
