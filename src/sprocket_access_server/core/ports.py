from __future__ import annotations

from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol


@dataclass(frozen=True)
class GitHubIdentity:
    user_id: str
    login: str
    email: str = ""


class GitHubIdentityProvider(Protocol):
    def verify_access_token(self, access_token: str) -> GitHubIdentity: ...


class GitHubIdentityError(ValueError):
    """A GitHub identity request failed with a known upstream outcome."""

    def __init__(self, message: str, *, status: int | None = None, unavailable: bool = False):
        super().__init__(message)
        self.status = status
        self.unavailable = unavailable


class Database(Protocol):
    def initialize(self) -> None: ...


class ObjectStorage(Protocol):
    def put(self, source: BinaryIO, *, digest: str, size: int) -> None: ...

    def open(self, digest: str) -> BinaryIO: ...

    def exists(self, digest: str) -> bool: ...

    def delete(self, digest: str) -> None: ...


class DirectUploadStorage(Protocol):
    def create_upload(self, *, object_key: str, size: int, content_type: str, expires_in: int) -> dict[str, Any]: ...

    def confirm_upload(self, *, object_key: str, digest: str, size: int) -> None: ...

    def open(self, digest: str) -> BinaryIO: ...

    def exists(self, digest: str) -> bool: ...


class AuditSink(Protocol):
    def record(self, *, actor: str, action: str, target: str, metadata: dict[str, Any], now: int) -> None: ...
