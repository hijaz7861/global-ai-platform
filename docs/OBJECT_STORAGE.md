# Portable object storage

HIJAZ ONE exposes a provider-neutral `ObjectStorage` contract in `app/storage.py` with two adapters:

- `LocalFilesystemObjectStorage` for local development and deterministic tests.
- `S3CompatibleObjectStorage` for AWS S3 and S3-compatible APIs, including services such as MinIO and Cloudflare R2 when configured with a compatible endpoint and credentials.

## Explicit provider setup

The S3 adapter accepts an injected boto3-compatible client. Importing the core application does not create a cloud client, read storage credentials, or make a network request. To use the optional convenience factory, install `requirements-storage.txt` and call `S3CompatibleObjectStorage.from_boto3(...)` explicitly with owner-provided configuration. Prefer the host's workload identity/credential chain where available; never commit credentials or include them in logs.

No provider is enabled by default. This adapter is not yet wired to project, procurement, property, education, or user-upload endpoints. It does not copy Hatchable media, infer ownership, issue download URLs, or replace authorization checks.

## Security and data contract

- Object keys are relative POSIX paths, at most 1,024 UTF-8 bytes, and reject absolute paths, empty segments, dot segments, backslashes, and NUL bytes.
- Writes and reads enforce a configurable object-size ceiling (default 25 MiB; maximum 1 GiB).
- Content types reject CR/LF and are metadata only; callers must validate file content independently rather than trusting a supplied MIME type.
- Local writes use a temporary file followed by atomic replacement and reject resolved symlink escapes. The local adapter is not a multi-host shared store and does not provide encryption-at-rest or backup policy.
- S3 operations propagate provider failures. Only recognized not-found responses make `exists()` return false.
- Tenant/object authorization, malware scanning, retention, encryption policy, signed download URLs, versioning, backup/restore and audit integration remain responsibilities of the service layer and are release gates.

## Verification

`tests/test_object_storage.py` covers local/S3 round trips, path traversal, symlink escape, size limits, content-type header injection, deletion idempotency, and propagation of non-404 provider errors.

This is an adapter foundation only. No bucket, cloud account, credential, endpoint, external service, or Hatchable resource has been configured or contacted.
