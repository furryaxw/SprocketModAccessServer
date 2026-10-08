from __future__ import annotations

import hashlib
import os
from io import BytesIO
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO
from urllib.parse import urlparse


def _origin_of(url: str) -> str:
    """URL 的 origin（`scheme://host[:port]`）；解析不出来时为空串。"""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


# S3 兼容后端把"对象不在"表达成异常：识别它，其余错误照常抛出，别把故障说成不存在。
_MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "NotFound", "404"})


def _missing_object(exc: Exception) -> bool:
    error = getattr(exc, "response", None)
    if not isinstance(error, dict):
        return False
    return str((error.get("Error") or {}).get("Code", "")) in _MISSING_OBJECT_CODES


class LocalFileObjectStorage:
    # 本地存储没有可外发的地址：下载由服务端代理字节，因此没有可声明的下载源。
    download_origin = ""

    def __init__(self, root: Path):
        self.root = root.expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise ValueError("object digest must be a SHA-256 hex digest")
        return self.root / digest.lower()

    def put(self, source: BinaryIO, *, digest: str, size: int) -> None:
        if size < 0:
            raise ValueError("object size must not be negative")
        target = self._path(digest)
        temporary = target.with_suffix(".tmp")
        total = 0
        hasher = hashlib.sha256()
        try:
            with temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > size:
                        raise ValueError("object exceeds declared size")
                    hasher.update(chunk)
                    output.write(chunk)
            if total != size or hasher.hexdigest() != digest.lower():
                raise ValueError("object digest or size does not match declaration")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def open(self, digest: str) -> BinaryIO:
        return self._path(digest).open("rb")

    def download_redirect(self, digest: str) -> None:
        """本地存储把字节交给服务端代理，没有重定向。"""
        return None

    def exists(self, digest: str) -> bool:
        return self._path(digest).is_file()

    def delete(self, digest: str) -> None:
        """按摘要删字节；对象已不在时是空操作（回收是幂等的）。"""
        self._path(digest).unlink(missing_ok=True)

    def create_upload(self, *, object_key: str, size: int, content_type: str, expires_in: int) -> dict[str, object]:
        if not object_key or "/" in object_key or "\\" in object_key or object_key in {".",
                                                                                       ".."} or size < 1 or expires_in < 1:
            raise ValueError("upload parameters are invalid")
        staging = self.root / "uploads"
        staging.mkdir(exist_ok=True)
        path = staging / object_key
        path.touch(exist_ok=False)
        return {"upload_url": f"/v1/package-uploads/{object_key}", "object_key": object_key, "expires_in": expires_in,
                "content_type": content_type, "size": size}

    def write_upload(self, *, object_key: str, content: bytes) -> None:
        key = self._validate_upload_key(object_key)
        path = self.root / "uploads" / key
        if not path.is_file():
            raise FileNotFoundError(object_key)
        path.write_bytes(content)

    @staticmethod
    def _validate_upload_key(object_key: str) -> str:
        if not object_key or "/" in object_key or "\\" in object_key or object_key in {".", ".."}:
            raise ValueError("upload object key is invalid")
        return object_key

    def confirm_upload(self, *, object_key: str, digest: str, size: int) -> None:
        source = self.root / "uploads" / object_key
        with source.open("rb") as stream:
            self.put(stream, digest=digest, size=size)
        source.unlink(missing_ok=True)


class S3ObjectStorage:
    """S3-compatible content-addressed storage; client is injectable for tests."""

    def __init__(self, bucket: str, *, prefix: str = "", endpoint_url: str | None = None, region: str | None = None,
                 force_path_style: bool = True, download_url_ttl: int = 300, client=None):
        if not bucket:
            raise ValueError("S3 bucket is required")
        if download_url_ttl < 1:
            raise ValueError("download URL TTL must be positive")
        if client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise RuntimeError("boto3 is required for S3 object storage") from exc
            client = boto3.client("s3", endpoint_url=endpoint_url,
                                  region_name=region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
                                  config=Config(s3={"addressing_style": "path" if force_path_style else "auto"}))
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.download_url_ttl = download_url_ttl
        # 重定向目标就是桶自己的地址：用一次签名取样得到确切的 origin，避免自己推寻址风格。
        self.download_origin = _origin_of(self.download_redirect("0" * 64))

    def _key(self, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
            raise ValueError("object digest must be a SHA-256 hex digest")
        return f"{self.prefix}/{value.lower()}" if self.prefix else value.lower()

    def put(self, source: BinaryIO, *, digest: str, size: int) -> None:
        if size < 0:
            raise ValueError("object size must not be negative")
        self.client.put_object(Bucket=self.bucket, Key=self._key(digest), Body=source, ContentLength=size)

    def open(self, digest: str) -> BinaryIO:
        """可随机访问的流：归档校验（魔数、zip 目录）要 seek，桶上的对象先落到临时文件。

        小对象留在内存，超过阈值就溢写到磁盘，因此大归档不会整份进内存。
        """
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=self._key(digest))["Body"]
        except Exception as exc:
            # 缺对象按 OSError 上报：下载端点据此回 404，而不是把后端异常漏成 500。
            if _missing_object(exc):
                raise FileNotFoundError(self._key(digest)) from exc
            raise
        spool = SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        try:
            while chunk := body.read(1024 * 1024):
                spool.write(chunk)
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()
        spool.seek(0)
        return spool

    def download_redirect(self, digest: str) -> str:
        """下载交给桶的短寿命签名 URL：字节不经过服务端。"""
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._key(digest)},
            ExpiresIn=self.download_url_ttl,
        )

    def exists(self, digest: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(digest))
            return True
        except Exception as exc:
            if _missing_object(exc):
                return False
            raise

    def delete(self, digest: str) -> None:
        """删对象；S3 的 delete 对不存在的键同样成功，回收因此可以重放。"""
        self.client.delete_object(Bucket=self.bucket, Key=self._key(digest))

    def create_upload(self, *, object_key: str, size: int, content_type: str, expires_in: int) -> dict[str, object]:
        if not object_key or size < 1 or expires_in < 1:
            raise ValueError("upload parameters are invalid")
        key = f"{self.prefix}/uploads/{object_key}" if self.prefix else f"uploads/{object_key}"
        url = self.client.generate_presigned_url("put_object",
                                                 Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type,
                                                         "ContentLength": size}, ExpiresIn=expires_in)
        return {"upload_url": url, "object_key": object_key, "expires_in": expires_in, "content_type": content_type,
                "size": size}

    def confirm_upload(self, *, object_key: str, digest: str, size: int) -> None:
        key = f"{self.prefix}/uploads/{object_key}" if self.prefix else f"uploads/{object_key}"
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            # 暂存对象不在是发布方的输入问题（400），不是服务端故障。
            if _missing_object(exc):
                raise ValueError("uploaded object is unavailable") from exc
            raise
        # 边读边校验：不把整份归档搬进内存，也不产生第二次上传的带宽。
        hasher = hashlib.sha256()
        total = 0
        body = response["Body"]
        try:
            while chunk := body.read(1024 * 1024):
                total += len(chunk)
                hasher.update(chunk)
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()
        if total != size or hasher.hexdigest() != digest.lower():
            raise ValueError("object digest or size does not match declaration")
        self.client.copy_object(Bucket=self.bucket, Key=self._key(digest),
                                CopySource={"Bucket": self.bucket, "Key": key})
        self.client.delete_object(Bucket=self.bucket, Key=key)
