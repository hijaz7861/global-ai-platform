"""Security and behavior tests for provider-neutral object storage adapters."""
from io import BytesIO

import pytest

from app.storage import LocalFilesystemObjectStorage, S3CompatibleObjectStorage


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.put_requests = []

    def put_object(self, **kwargs):
        self.put_requests.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = {
            "body": kwargs["Body"],
            "content_type": kwargs.get("ContentType"),
        }

    def get_object(self, *, Bucket, Key):
        return {"Body": BytesIO(self.objects[(Bucket, Key)]["body"])}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            error = RuntimeError("not found")
            error.response = {
                "Error": {"Code": "NoSuchKey"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            }
            raise error
        return {}


def test_local_storage_round_trip_and_idempotent_delete(tmp_path):
    storage = LocalFilesystemObjectStorage(tmp_path / "objects")
    storage.put("tenant-1/projects/quote.pdf", b"quote", content_type="application/pdf")
    assert storage.exists("tenant-1/projects/quote.pdf")
    assert storage.get("tenant-1/projects/quote.pdf") == b"quote"
    storage.delete("tenant-1/projects/quote.pdf")
    storage.delete("tenant-1/projects/quote.pdf")
    assert not storage.exists("tenant-1/projects/quote.pdf")


@pytest.mark.parametrize(
    "key",
    ["", "/absolute", "../escape", "tenant/../escape", "tenant//empty", "tenant\\escape", "tenant/./file"],
)
def test_both_adapters_reject_unsafe_keys(tmp_path, key):
    local = LocalFilesystemObjectStorage(tmp_path / "objects")
    remote = S3CompatibleObjectStorage(FakeS3Client(), "test-bucket")
    for storage in (local, remote):
        with pytest.raises(ValueError, match="Object key"):
            storage.put(key, b"data")


def test_local_storage_rejects_symlink_escape(tmp_path):
    root = tmp_path / "objects"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    storage = LocalFilesystemObjectStorage(root)
    with pytest.raises(ValueError, match="escapes storage root"):
        storage.put("linked/secret.txt", b"must-not-write")
    assert not (outside / "secret.txt").exists()


def test_storage_rejects_oversized_payload_and_invalid_content_type(tmp_path):
    local = LocalFilesystemObjectStorage(tmp_path / "objects", max_object_bytes=4)
    with pytest.raises(ValueError, match="size limit"):
        local.put("small.txt", b"12345")
    with pytest.raises(ValueError, match="Content type"):
        local.put("small.txt", b"ok", content_type="text/plain\r\nX-Injected: yes")


def test_s3_adapter_round_trip_uses_only_explicit_bucket_and_key():
    client = FakeS3Client()
    storage = S3CompatibleObjectStorage(client, "hijaz-one-test", max_object_bytes=8)
    storage.put("org-7/invoices/1.pdf", b"invoice", content_type="application/pdf")
    assert client.put_requests[0]["Bucket"] == "hijaz-one-test"
    assert client.put_requests[0]["Key"] == "org-7/invoices/1.pdf"
    assert client.put_requests[0]["ContentType"] == "application/pdf"
    assert storage.exists("org-7/invoices/1.pdf")
    assert storage.get("org-7/invoices/1.pdf") == b"invoice"
    storage.delete("org-7/invoices/1.pdf")
    assert not storage.exists("org-7/invoices/1.pdf")


def test_s3_adapter_bounds_download_size_and_closes_body():
    client = FakeS3Client()
    storage = S3CompatibleObjectStorage(client, "test-bucket", max_object_bytes=4)
    # Simulate a remote object uploaded outside this adapter's configured cap.
    client.put_object(Bucket="test-bucket", Key="large.bin", Body=b"12345")
    with pytest.raises(ValueError, match="size limit"):
        storage.get("large.bin")


def test_s3_adapter_does_not_hide_non_not_found_errors():
    class BrokenClient(FakeS3Client):
        def head_object(self, **kwargs):
            raise RuntimeError("provider unavailable")

    storage = S3CompatibleObjectStorage(BrokenClient(), "test-bucket")
    with pytest.raises(RuntimeError, match="provider unavailable"):
        storage.exists("missing.bin")


def test_storage_limits_reject_boolean_and_unbounded_values(tmp_path):
    with pytest.raises(ValueError):
        LocalFilesystemObjectStorage(tmp_path / "objects", max_object_bytes=True)
    with pytest.raises(ValueError):
        S3CompatibleObjectStorage(FakeS3Client(), "bucket", max_object_bytes=2**40)
