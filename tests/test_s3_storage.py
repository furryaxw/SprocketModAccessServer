from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from sprocket_access_server.infrastructure.storage.objects import LocalFileObjectStorage, S3ObjectStorage


class FakeBody:
    """按偏移推进的流式替身：`read(n)` 必须往前走，否则分块校验会转圈。"""

    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read(self, size: int = -1):
        if size is None or size < 0:
            chunk = self.data[self.offset:]
            self.offset = len(self.data)
            return chunk
        chunk = self.data[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self):
        return None


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.presigned = []

    def put_object(self, *, Bucket, Key, Body, ContentLength=None):
        self.objects[(Bucket, Key)] = Body.read()

    def copy_object(self, *, Bucket, Key, CopySource):
        self.objects[(Bucket, Key)] = self.objects[(CopySource["Bucket"], CopySource["Key"])]

    def get_object(self, *, Bucket, Key):
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError(Key)

    def delete_object(self, *, Bucket, Key):
        del self.objects[(Bucket, Key)]

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presigned.append({"operation": operation, "params": Params, "expires_in": ExpiresIn})
        return f"https://storage.test/{operation}?sig=stub"


class BackendError(Exception):
    """非"对象不在"的后端故障：必须原样上报，不能被当成不存在。"""

    def __init__(self, code: str = "AccessDenied"):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FailingS3(FakeS3):
    def __init__(self, code: str):
        super().__init__()
        self.code = code

    def head_object(self, *, Bucket, Key):
        raise BackendError(self.code)

    def get_object(self, *, Bucket, Key):
        raise BackendError(self.code)


class S3ErrorMappingTests(unittest.TestCase):
    def test_missing_object_reads_as_file_not_found(self):
        storage = S3ObjectStorage("bucket", client=FailingS3("NoSuchKey"))
        with self.assertRaises(FileNotFoundError):
            storage.open("a" * 64)

    def test_missing_object_exists_check_is_false(self):
        self.assertFalse(S3ObjectStorage("bucket", client=FailingS3("404")).exists("a" * 64))

    def test_backend_failure_is_not_reported_as_missing(self):
        storage = S3ObjectStorage("bucket", client=FailingS3("AccessDenied"))
        with self.assertRaises(BackendError):
            storage.open("a" * 64)
        with self.assertRaises(BackendError):
            storage.exists("a" * 64)

    def test_missing_staged_upload_is_a_value_error(self):
        storage = S3ObjectStorage("bucket", client=FailingS3("NoSuchKey"))
        with self.assertRaisesRegex(ValueError, "unavailable"):
            storage.confirm_upload(object_key="draft", digest="a" * 64, size=1)


class S3StorageTests(unittest.TestCase):
    def test_local_upload_uses_http_endpoint_and_writes_staged_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalFileObjectStorage(Path(directory))
            upload = storage.create_upload(
                object_key="draft",
                size=7,
                content_type="application/zip",
                expires_in=60,
            )
            storage.write_upload(object_key="draft", content=b"archive")
            self.assertEqual(upload["upload_url"], "/v1/package-uploads/draft")
            self.assertEqual((Path(directory) / "uploads" / "draft").read_bytes(), b"archive")

    def test_confirm_copies_verified_upload_to_digest_key(self):
        client = FakeS3()
        storage = S3ObjectStorage("bucket", prefix="mods", client=client)
        content = b"archive"
        digest = hashlib.sha256(content).hexdigest()
        storage.create_upload(object_key="draft", size=len(content), content_type="application/zip", expires_in=60)
        client.objects[("bucket", "mods/uploads/draft")] = content
        storage.confirm_upload(object_key="draft", digest=digest, size=len(content))
        self.assertTrue(storage.exists(digest))
        self.assertNotIn(("bucket", "mods/uploads/draft"), client.objects)

    def test_presigned_upload_contains_expiry(self):
        client = FakeS3()
        storage = S3ObjectStorage("bucket", client=client)
        client.presigned.clear()
        result = storage.create_upload(object_key="draft", size=3, content_type="application/zip", expires_in=30)
        self.assertEqual(result["upload_url"], "https://storage.test/put_object?sig=stub")
        self.assertEqual(client.presigned, [{
            "operation": "put_object",
            "params": {"Bucket": "bucket", "Key": "uploads/draft", "ContentType": "application/zip",
                       "ContentLength": 3},
            "expires_in": 30,
        }])

    def test_presigned_download_points_at_the_digest_key(self):
        client = FakeS3()
        storage = S3ObjectStorage("bucket", prefix="mods", client=client, download_url_ttl=120)
        digest = hashlib.sha256(b"archive").hexdigest()
        client.presigned.clear()
        self.assertEqual(storage.download_redirect(digest), "https://storage.test/get_object?sig=stub")
        self.assertEqual(client.presigned, [{
            "operation": "get_object",
            "params": {"Bucket": "bucket", "Key": f"mods/{digest}"},
            "expires_in": 120,
        }])

    def test_open_returns_a_seekable_stream(self):
        client = FakeS3()
        storage = S3ObjectStorage("bucket", client=client)
        content = b"PK\x03\x04-archive"
        digest = hashlib.sha256(content).hexdigest()
        client.objects[("bucket", digest)] = content
        with storage.open(digest) as stream:
            self.assertEqual(stream.read(4), b"PK\x03\x04")
            stream.seek(0)
            self.assertEqual(stream.read(), content)

    def test_download_origin_comes_from_the_signed_url(self):
        self.assertEqual(S3ObjectStorage("bucket", client=FakeS3()).download_origin, "https://storage.test")

    def test_local_storage_has_no_redirect_or_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalFileObjectStorage(Path(directory))
            self.assertIsNone(storage.download_redirect("a" * 64))
            self.assertEqual(storage.download_origin, "")

    def test_download_ttl_must_be_positive(self):
        with self.assertRaises(ValueError):
            S3ObjectStorage("bucket", client=FakeS3(), download_url_ttl=0)


if __name__ == "__main__":
    unittest.main()
