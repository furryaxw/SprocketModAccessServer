from __future__ import annotations

import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sprocket_access_server.infrastructure.security.signing import canonical_json, load_private_key, public_identity, \
    sign_manifest


class SigningTests(unittest.TestCase):
    def pem(self, key: Ed25519PrivateKey) -> str:
        return key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")

    def test_file_configuration_has_priority_over_environment_pem(self) -> None:
        file_key = Ed25519PrivateKey.generate()
        env_key = Ed25519PrivateKey.generate()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "signing.pem"
            path.write_text(self.pem(file_key), encoding="ascii")
            loaded = load_private_key(file_path=str(path), pem=self.pem(env_key))
        self.assertEqual(
            public_identity(loaded, "key-1")["public_key"],
            public_identity(file_key, "key-1")["public_key"],
        )

    def test_environment_pem_is_supported_when_file_is_not_configured(self) -> None:
        key = Ed25519PrivateKey.generate()
        loaded = load_private_key(file_path="", pem=self.pem(key))
        self.assertEqual(public_identity(loaded, "key-1")["key_id"], "key-1")

    def test_missing_or_wrong_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not configured"):
            load_private_key(file_path="", pem="")
        with self.assertRaisesRegex(ValueError, "invalid"):
            load_private_key(file_path="", pem="not-a-key")

    def test_manifest_signature_is_detached_canonical_json(self) -> None:
        key = Ed25519PrivateKey.generate()
        manifest = {"id": "mod.a", "version": "1.0.0"}
        signed = sign_manifest(manifest, key, "key-1")
        signature = signed.pop("signature")
        self.assertEqual(signature["format"], "detached-canonical-json")
        value = base64.urlsafe_b64decode(signature["signature"] + "==")
        key.public_key().verify(value, canonical_json(manifest))


if __name__ == "__main__":
    unittest.main()
