from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical_json(value: Any) -> bytes:
    # allow_nan=False：含 NaN/Infinity 的条目无法被客户端解析，验签前就应拒绝。
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def load_private_key(*, file_path: str | None = None, pem: str | None = None) -> Ed25519PrivateKey:
    configured_path = os.environ.get("SMAS_SIGNING_PRIVATE_KEY_FILE", "") if file_path is None else file_path
    configured_pem = os.environ.get("SMAS_SIGNING_PRIVATE_KEY", "") if pem is None else pem
    raw: bytes
    if configured_path.strip():
        try:
            raw = Path(configured_path).expanduser().read_bytes()
        except OSError as exc:
            raise ValueError("signing private key file cannot be read") from exc
    elif configured_pem.strip():
        raw = configured_pem.encode("utf-8")
    else:
        raise ValueError("signing private key is not configured")
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except (ValueError, TypeError) as exc:
        raise ValueError("signing private key is invalid") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing private key must be Ed25519")
    return key


def public_identity(private_key: Ed25519PrivateKey, key_id: str) -> dict[str, str]:
    if not key_id.strip():
        raise ValueError("signing key ID is required")
    public = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return {
        "algorithm": "ed25519",
        "encoding": "base64url",
        "key_id": key_id,
        "public_key": base64.urlsafe_b64encode(public).decode("ascii").rstrip("="),
        "fingerprint": "sha256:" + hashlib.sha256(public).hexdigest(),
    }


def sign_manifest(manifest: dict[str, Any], private_key: Ed25519PrivateKey, key_id: str) -> dict[str, Any]:
    signature = private_key.sign(canonical_json(manifest))
    return {
        **manifest,
        "signature": {
            "format": "detached-canonical-json",
            "algorithm": "ed25519",
            "encoding": "base64url",
            "key_id": key_id,
            "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        },
    }
