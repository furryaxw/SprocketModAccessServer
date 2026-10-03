from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 2


@dataclass(frozen=True)
class ServerInfo:
    server_id: str
    name: str
    signing_identity: dict[str, Any] | None
    identity_policy: dict[str, Any]
    # 允许把下载指向哪些源：裸主机名表示仅接受 https 并按主机名匹配，完整 origin 按 scheme/host/port 精确匹配。
    download_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.server_id.strip() or not self.name.strip():
            raise ValueError("server identity is incomplete")
        if self.signing_identity is not None and not isinstance(self.signing_identity, dict):
            raise ValueError("signing identity must be an object")
        if any(not str(item).strip() for item in self.download_origins):
            raise ValueError("download origins must be non-empty")

    def payload(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "server_id": self.server_id,
            "name": self.name,
            "download_origins": list(self.download_origins),
            "signing_identity": self.signing_identity,
            "identity_policy": self.identity_policy,
        }
