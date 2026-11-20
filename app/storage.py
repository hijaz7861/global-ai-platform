"""Provider-neutral object storage adapters for portable HIJAZ ONE deployments.

The S3 adapter accepts a boto3-compatible client rather than creating credentials
or network clients implicitly. The optional boto3 factory is explicit and lazy.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Protocol, runtime_checkable


_DEFAULT_MAX_OBJECT_BYTES = 25 * 1024 * 1024


def _validate_key(key: str) -> str:
    """Validate a portable object key without normalizing unsafe path segments."""
    if not isinstance(key, str) or not key or "\x00" in key or "\\" in key:
        raise ValueError("Object key is invalid")
    if key.startswith("/") or len(key.encode("utf-8")) > 1024:
        raise ValueError("Object key is invalid")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Object key is invalid")
    return str(PurePosixPath(key))


def _validate_content_type(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    if not isinstance(content_type, str) or not content_type or len(content_type) > 255:
        raise ValueError("Content type is invalid")
    if "\r" in content_type or "\n" in content_type:
        raise ValueError("Content type is invalid")
    return content_type


def _validate_payload(data: bytes, max_object_bytes: int) -> bytes:
    if not isinstance(data, bytes):
        raise TypeError("Object payload must be bytes")
    if len(data) > max_object_bytes:
        raise ValueError("Object exceeds configured size limit")
    return data


def _validate_limit(max_object_bytes: int) -> int:
    if isinstance(max_object_bytes, bool) or not isinstance(max_object_bytes, int):
        raise ValueError("Object size limit must be an integer")
    if not 1 <= max_object_bytes <= 1024 * 1024 * 1024:
        raise ValueError("Object size limit is outside the supported range")
    return max_object_bytes


@runtime_checkable
class ObjectStorage(Protocol):
    """Minimal object storage contract; callers own authorization and metadata."""

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalFilesystemObjectStorage:
    """Atomic filesystem adapter intended for local development and tests."""

    def __init__(
        self, root: str | Path, *, max_object_bytes: int = _DEFAULT_MAX_OBJECT_BYTES
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.max_object_bytes = _validate_limit(max_object_bytes)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe_key = _validate_key(key)
        candidate = (self.root / PurePosixPath(safe_key)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Object key escapes storage root") from exc
        return candidate

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        _validate_content_type(content_type)
        payload = _validate_payload(data, self.max_object_bytes)
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Re-check after parent creation so pre-existing symlinks cannot redirect
        # a write outside the configured root.
        destination = self._path(key)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=".hijaz-upload-", delete=False
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(payload)
                temporary.flush()
            Path(temporary_name).replace(destination)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        with path.open("rb") as source:
            payload = source.read(self.max_object_bytes + 1)
        if len(payload) > self.max_object_bytes:
            raise ValueError("Object exceeds configured size limit")
        return payload

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3CompatibleObjectStorage:
    """S3 API adapter for AWS S3 and compatible services (MinIO, R2, etc.).

    A boto3-compatible client is injected, so this module does not choose a
    provider, read credentials, or perform network access during import.
    """

    def __init__(
        self,
        client: Any,
        bucket: str,
        *,
        max_object_bytes: int = _DEFAULT_MAX_OBJECT_BYTES,
    ) -> None:
        if client is None or not isinstance(bucket, str) or not bucket.strip():
            raise ValueError("An S3 client and non-empty bucket are required")
        self.client = client
        self.bucket = bucket.strip()
        self.max_object_bytes = _validate_limit(max_object_bytes)

    @classmethod
    def from_boto3(
        cls,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        max_object_bytes: int = _DEFAULT_MAX_OBJECT_BYTES,
    ) -> "S3CompatibleObjectStorage":
        """Explicitly create an S3 client; credentials remain caller-supplied."""
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "Install the optional storage requirements to use the boto3 adapter"
            ) from exc
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )
        return cls(client, bucket, max_object_bytes=max_object_bytes)

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        safe_key = _validate_key(key)
        payload = _validate_payload(data, self.max_object_bytes)
        safe_content_type = _validate_content_type(content_type)
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": safe_key,
            "Body": payload,
        }
        if safe_content_type is not None:
            request["ContentType"] = safe_content_type
        self.client.put_object(**request)

    def get(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=_validate_key(key))
        body = response["Body"]
        try:
            payload = body.read(self.max_object_bytes + 1)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if len(payload) > self.max_object_bytes:
            raise ValueError("Object exceeds configured size limit")
        return payload

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_validate_key(key))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_validate_key(key))
            return True
        except Exception as exc:
            response: Mapping[str, Any] = getattr(exc, "response", {}) or {}
            error = response.get("Error", {})
            metadata = response.get("ResponseMetadata", {})
            code = str(error.get("Code", ""))
            status = metadata.get("HTTPStatusCode")
            if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
                return False
            raise
