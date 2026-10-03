from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _bootstrap_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


_bootstrap_imports()

from sprocket_access_server.core.runtime import build_app


def _tree(resources: dict[str, object], node: str, actions: list[str]) -> None:
    current = resources
    parts = node.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if child is None:
            child = {}
            current[part] = child
        elif not isinstance(child, dict):
            child = {"_permissions": child}
            current[part] = child
        current = child
    leaf = sorted(set(actions))
    name = parts[-1]
    existing = current.get(name)
    if existing is None:
        current[name] = leaf
    elif isinstance(existing, dict):
        existing["_permissions"] = leaf
    else:
        current[name] = {"_permissions": leaf}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the current permission tree as JSON")
    parser.add_argument("-o", "--output", default="tree.json")
    parser.add_argument("--base-dir", default=Path.cwd(), type=Path)
    args = parser.parse_args(argv)

    base_dir = args.base_dir.expanduser().resolve()
    _load_dotenv(base_dir / ".env")
    _ensure_export_environment(base_dir)
    app = build_app(base_dir=base_dir)
    context = app.state.module_context

    resources: dict[str, object] = {}
    for definition in sorted(context.resources.definitions(), key=lambda item: item.node):
        actions = [
            *(["grant"] if definition.auto_grant else []),
            *(operation.action for operation in definition.operations if operation.requires_permission),
        ]
        if not actions:
            continue
        _tree(resources, definition.node, actions)

    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = Path.cwd() / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"resources": resources}, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    return 0


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip().strip('"').strip("'")


def _ensure_export_environment(base_dir: Path) -> None:
    os.environ.setdefault("SMAS_DATABASE_URL", "sqlite:///data/access.db")
    os.environ.setdefault("SMAS_OBJECT_STORAGE_URL", "file:./data/packages")
    os.environ.setdefault("SMAS_SESSION_PEPPER", "permission-tree-export-session-pepper")
    os.environ.setdefault("SMAS_KEY_PEPPER", "permission-tree-export-key-pepper")
    os.environ.setdefault("SMAS_DOWNLOAD_TOKEN_SECRET", "permission-tree-export-download-token-secret")
    os.environ.setdefault("SMAS_SIGNING_KEY_ID", "permission-tree-export")
    key_file = os.environ.get("SMAS_SIGNING_PRIVATE_KEY_FILE", "").strip()
    if key_file:
        key_path = Path(key_file).expanduser()
        if not key_path.is_absolute():
            key_path = base_dir / key_path
        if key_path.is_file():
            os.environ["SMAS_SIGNING_PRIVATE_KEY_FILE"] = str(key_path)
            return
        os.environ.pop("SMAS_SIGNING_PRIVATE_KEY_FILE", None)
    if not os.environ.get("SMAS_SIGNING_PRIVATE_KEY", "").strip():
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        os.environ["SMAS_SIGNING_PRIVATE_KEY"] = pem


if __name__ == "__main__":
    raise SystemExit(main())
