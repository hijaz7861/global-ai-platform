"""Deterministic validation and hashing for proposed repository file changes.

This module does not apply patches, execute code, create commits, or authorize releases.
It creates a bounded, canonical artifact that later sandbox and approval stages can verify.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from collections.abc import Iterable


_MAX_FILES = 20
_MAX_FILE_BYTES = 65_536
_MAX_TOTAL_BYTES = 262_144
_ALLOWED_MODE = "100644"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._@+-]+$")

# These paths can change identity, authorization, cryptography, deployment, data
# integrity, or dependency trust. They are intentionally blocked in this first
# proposal stage and require a separate, explicitly reviewed workflow.
_SENSITIVE_SEGMENTS = {
    ".github", ".git", "auth", "authentication", "authorization", "security",
    "secrets", "secret", "migrations", "alembic", "payments", "payment",
    "ledger", "accounting", "crypto", "cryptography", "models",
}
_SENSITIVE_NAMES = {
    ".env", ".env.example", "dockerfile", "docker-compose.yml",
    "compose.yml", "compose.yaml", "pyproject.toml", "poetry.lock",
    "uv.lock", "requirements.txt", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock",
}


class ProposalValidationError(ValueError):
    """A proposed repository change violates bounded proposal policy."""


@dataclass(frozen=True)
class ProposedFile:
    path: str
    content: str
    mode: str = _ALLOWED_MODE


@dataclass(frozen=True)
class ValidatedProposal:
    artifact: bytes
    sha256: str
    paths: tuple[str, ...]
    risk_level: str
    total_bytes: int


def _validate_path(path: str, *, allow_sensitive_paths: bool) -> str:
    if not isinstance(path, str) or not path or len(path) > 240:
        raise ProposalValidationError("invalid_path")
    if "\\" in path or path.startswith("/") or "\x00" in path:
        raise ProposalValidationError("unsafe_path")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ProposalValidationError("unsafe_path")
    if any(not _SAFE_SEGMENT.fullmatch(part) for part in parts):
        raise ProposalValidationError("unsafe_path")
    lowered = [part.casefold() for part in parts]
    filename = lowered[-1]
    sensitive = (
        any(part in _SENSITIVE_SEGMENTS for part in lowered)
        or filename in _SENSITIVE_NAMES
        or filename.endswith((".pem", ".key", ".p12", ".pfx"))
        or filename.startswith((".env", "id_rsa"))
    )
    if sensitive and not allow_sensitive_paths:
        raise ProposalValidationError("sensitive_path_requires_separate_review")
    return "/".join(parts)


def validate_proposal(
    files: Iterable[ProposedFile],
    *,
    max_files: int = _MAX_FILES,
    max_file_bytes: int = _MAX_FILE_BYTES,
    max_total_bytes: int = _MAX_TOTAL_BYTES,
    allow_sensitive_paths: bool = False,
) -> ValidatedProposal:
    """Validate a bounded set of new/replacement UTF-8 text files and hash it.

    Deletions, binary blobs, symlinks, executable modes, and sensitive paths are
    not supported. The returned digest covers the exact canonical artifact bytes.
    """
    if not 1 <= max_files <= _MAX_FILES:
        raise ProposalValidationError("invalid_file_limit")
    if not 1 <= max_file_bytes <= _MAX_FILE_BYTES:
        raise ProposalValidationError("invalid_per_file_limit")
    if not 1 <= max_total_bytes <= _MAX_TOTAL_BYTES:
        raise ProposalValidationError("invalid_total_limit")

    items = list(files)
    if not items:
        raise ProposalValidationError("empty_proposal")
    if len(items) > max_files:
        raise ProposalValidationError("too_many_files")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    total_bytes = 0
    docs_only = True
    sensitive_found = False

    for item in items:
        if not isinstance(item, ProposedFile):
            raise ProposalValidationError("invalid_file_record")
        path = _validate_path(item.path, allow_sensitive_paths=allow_sensitive_paths)
        lowered_parts = [part.casefold() for part in path.split("/")]
        filename = lowered_parts[-1]
        if (
            any(part in _SENSITIVE_SEGMENTS for part in lowered_parts)
            or filename in _SENSITIVE_NAMES
            or filename.endswith((".pem", ".key", ".p12", ".pfx"))
            or filename.startswith((".env", "id_rsa"))
        ):
            sensitive_found = True
        key = path.casefold()
        if key in seen:
            raise ProposalValidationError("duplicate_path")
        seen.add(key)
        if item.mode != _ALLOWED_MODE:
            raise ProposalValidationError("unsupported_file_mode")
        if not isinstance(item.content, str):
            raise ProposalValidationError("text_content_required")
        try:
            encoded = item.content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ProposalValidationError("invalid_utf8_content") from exc
        if b"\x00" in encoded:
            raise ProposalValidationError("binary_content_not_allowed")
        if len(encoded) > max_file_bytes:
            raise ProposalValidationError("file_too_large")
        total_bytes += len(encoded)
        if total_bytes > max_total_bytes:
            raise ProposalValidationError("proposal_too_large")
        if not path.casefold().startswith("docs/") and not path.casefold().startswith("tests/"):
            docs_only = False
        normalized.append({"path": path, "mode": _ALLOWED_MODE, "content": item.content})

    normalized.sort(key=lambda row: row["path"])
    artifact = json.dumps(
        {"schema_version": 1, "files": normalized},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ValidatedProposal(
        artifact=artifact,
        sha256=sha256(artifact).hexdigest(),
        paths=tuple(row["path"] for row in normalized),
        risk_level="high" if sensitive_found else ("low" if docs_only else "medium"),
        total_bytes=total_bytes,
    )


def materialize_proposal(
    proposal: ValidatedProposal,
    destination: str,
) -> tuple[str, ...]:
    """Safely unpack a digest-verified proposal into a pre-created empty workspace.

    The workspace must be private to the caller. This function never applies the
    files to a repository checkout and never executes candidate code.
    """
    from pathlib import Path

    if not isinstance(proposal, ValidatedProposal) or not isinstance(proposal.artifact, bytes):
        raise ProposalValidationError("invalid_proposal_artifact")
    actual_digest = sha256(proposal.artifact).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", proposal.sha256) or actual_digest != proposal.sha256:
        raise ProposalValidationError("proposal_digest_mismatch")

    try:
        payload = json.loads(proposal.artifact.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProposalValidationError("invalid_proposal_encoding") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "files"}
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or not isinstance(payload["files"], list)
    ):
        raise ProposalValidationError("invalid_proposal_schema")

    files: list[ProposedFile] = []
    for row in payload["files"]:
        if not isinstance(row, dict) or set(row) != {"path", "mode", "content"}:
            raise ProposalValidationError("invalid_proposal_file")
        files.append(ProposedFile(path=row["path"], mode=row["mode"], content=row["content"]))

    verified = validate_proposal(files, allow_sensitive_paths=True)
    if (
        verified.artifact != proposal.artifact
        or verified.sha256 != proposal.sha256
        or verified.paths != proposal.paths
        or verified.total_bytes != proposal.total_bytes
        or verified.risk_level != proposal.risk_level
    ):
        raise ProposalValidationError("noncanonical_proposal_artifact")

    root = Path(destination)
    if root.is_symlink() or not root.exists() or not root.is_dir():
        raise ProposalValidationError("workspace_must_be_existing_real_directory")
    if any(root.iterdir()):
        raise ProposalValidationError("workspace_must_be_empty")
    root = root.resolve(strict=True)

    written: list[str] = []
    for item in files:
        parts = item.path.split("/")
        parent = root
        for segment in parts[:-1]:
            parent = parent / segment
            if parent.is_symlink():
                raise ProposalValidationError("workspace_symlink_detected")
            parent.mkdir(exist_ok=True)
            if parent.is_symlink() or not parent.is_dir():
                raise ProposalValidationError("workspace_path_conflict")
            if not parent.resolve(strict=True).is_relative_to(root):
                raise ProposalValidationError("workspace_path_escape")
        target = parent / parts[-1]
        if target.exists() or target.is_symlink():
            raise ProposalValidationError("workspace_file_conflict")
        try:
            with target.open("xb") as stream:
                stream.write(item.content.encode("utf-8"))
        except OSError as exc:
            raise ProposalValidationError("workspace_write_failed") from exc
        written.append(item.path)
    return tuple(written)
