"""Bounded, read-only GitHub repository context loader for autonomous proposals."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import re
from collections.abc import Collection
from urllib.parse import quote

import httpx


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_SUFFIXES = {
    ".py", ".md", ".rst", ".toml", ".yaml", ".yml", ".json",
    ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".sql",
}
_BLOCKED_SEGMENTS = {
    ".git", ".github", "auth", "authentication", "authorization",
    "security", "secrets", "secret", "migrations", "alembic",
    "payments", "payment", "ledger", "accounting", "crypto",
    "cryptography", "certificates", "keys",
}
_BLOCKED_NAMES = {
    ".env", ".env.example", "id_rsa", "credentials.json",
    "secrets.json", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "poetry.lock", "uv.lock",
}


class RepositoryReadError(RuntimeError):
    """Sanitized failure while loading bounded repository context."""


@dataclass(frozen=True)
class RepositoryContextFile:
    path: str
    content: str
    blob_sha: str


@dataclass(frozen=True)
class RepositorySnapshot:
    repository: str
    default_branch: str
    commit_sha: str
    files: tuple[RepositoryContextFile, ...]


class GitHubRepositoryReader:
    """Read a bounded set of UTF-8 source files using a contents-read-only token."""

    def __init__(
        self,
        *,
        token: str,
        allowed_repositories: Collection[str],
        client: httpx.Client | None = None,
        max_files: int = 8,
        max_file_bytes: int = 16_384,
        max_total_bytes: int = 81_920,
    ) -> None:
        if not isinstance(token, str) or len(token) < 20:
            raise ValueError("A GitHub read token is required")
        allowed = frozenset(allowed_repositories)
        if not allowed or any(not isinstance(item, str) or not _REPOSITORY.fullmatch(item) for item in allowed):
            raise ValueError("An explicit owner/repository allowlist is required")
        if not 1 <= max_files <= 12 or not 1024 <= max_file_bytes <= 65_536:
            raise ValueError("Repository context limits are outside supported bounds")
        if not max_file_bytes <= max_total_bytes <= 131_072:
            raise ValueError("Repository context total limit is outside supported bounds")
        self._allowed = allowed
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes
        self._owns_client = client is None
        if client is not None and str(client.base_url).rstrip("/") != "https://api.github.com":
            raise ValueError("GitHub reader client must use the official api.github.com origin")
        self._client = client or httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "global-ai-platform-readonly-repository-reader",
            },
            timeout=httpx.Timeout(15.0),
            follow_redirects=False,
        )
        self._client.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "global-ai-platform-readonly-repository-reader",
        })
        self._client.follow_redirects = False
        self._client.timeout = httpx.Timeout(15.0)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get(self, path: str) -> dict:
        try:
            response = self._client.get(path)
        except httpx.HTTPError:
            raise RepositoryReadError("github_read_transport_error") from None
        if not 200 <= response.status_code < 300:
            raise RepositoryReadError(f"github_read_http_{response.status_code}")
        if len(response.content) > 2_000_000:
            raise RepositoryReadError("github_read_response_too_large")
        try:
            data = response.json()
        except ValueError:
            raise RepositoryReadError("github_read_invalid_json") from None
        if not isinstance(data, dict):
            raise RepositoryReadError("github_read_invalid_response_shape")
        return data

    @staticmethod
    def _safe_source_path(path: str) -> bool:
        parts = path.split("/")
        lowered = [part.casefold() for part in parts]
        filename = lowered[-1]
        suffix = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
        return (
            not path.startswith("/")
            and all(part not in {"", ".", ".."} for part in parts)
            and not any(part.startswith(".") or part in _BLOCKED_SEGMENTS for part in lowered)
            and filename not in _BLOCKED_NAMES
            and not filename.startswith((".env", "id_rsa"))
            and suffix in _ALLOWED_SUFFIXES
        )

    @staticmethod
    def _rank(path: str, terms: set[str]) -> tuple[int, int, str]:
        path_terms = set(re.findall(r"[a-zA-Z0-9]+", path.casefold()))
        overlap = len(path_terms & terms)
        preferred = 1 if path.casefold() in {"readme.md", "readme.rst"} else 0
        return (-overlap, -preferred, path.casefold())

    def read_context(self, repository: str, *, goal: str) -> RepositorySnapshot:
        """Read relevant, non-sensitive source files from the default branch only."""
        if repository not in self._allowed or not _REPOSITORY.fullmatch(repository):
            raise RepositoryReadError("repository_not_allowlisted")
        if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 5_000:
            raise RepositoryReadError("invalid_repository_read_goal")
        owner, repo = repository.split("/", 1)
        root = f"/repos/{owner}/{repo}"
        repo_data = self._get(root)
        branch = repo_data.get("default_branch")
        if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", branch):
            raise RepositoryReadError("invalid_default_branch")
        ref_data = self._get(f"{root}/git/ref/heads/{quote(branch, safe='')}")
        commit_sha = (ref_data.get("object") or {}).get("sha")
        if not isinstance(commit_sha, str) or not _GIT_SHA.fullmatch(commit_sha):
            raise RepositoryReadError("invalid_default_commit")
        commit = self._get(f"{root}/git/commits/{commit_sha}")
        tree_sha = (commit.get("tree") or {}).get("sha")
        if not isinstance(tree_sha, str) or not _GIT_SHA.fullmatch(tree_sha):
            raise RepositoryReadError("invalid_default_tree")
        tree = self._get(f"{root}/git/trees/{tree_sha}?recursive=1")
        entries = tree.get("tree")
        if tree.get("truncated") is True or not isinstance(entries, list):
            raise RepositoryReadError("repository_tree_unavailable_or_truncated")

        candidates = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != "blob":
                continue
            path, blob_sha, size = entry.get("path"), entry.get("sha"), entry.get("size")
            if (
                not isinstance(path, str)
                or not self._safe_source_path(path)
                or not isinstance(blob_sha, str)
                or not _GIT_SHA.fullmatch(blob_sha)
                or not isinstance(size, int)
                or size < 0
                or size > self._max_file_bytes
            ):
                continue
            candidates.append((path, blob_sha, size))

        terms = {term for term in re.findall(r"[a-zA-Z0-9_]+", goal.casefold()) if len(term) > 2}
        candidates.sort(key=lambda row: self._rank(row[0], terms))
        selected = []
        total = 0
        for path, blob_sha, size in candidates:
            if len(selected) >= self._max_files or total + size > self._max_total_bytes:
                continue
            selected.append((path, blob_sha, size))
            total += size
        if not selected:
            raise RepositoryReadError("no_safe_repository_context_files")

        files: list[RepositoryContextFile] = []
        actual_total = 0
        for path, blob_sha, expected_size in selected:
            blob = self._get(f"{root}/git/blobs/{blob_sha}")
            if blob.get("encoding") != "base64" or not isinstance(blob.get("content"), str):
                continue
            try:
                raw = base64.b64decode("".join(blob["content"].split()), validate=True)
            except (ValueError, TypeError):
                continue
            if len(raw) != expected_size or b"\x00" in raw:
                continue
            # Verify the Git blob identity, not merely the JSON's claimed SHA.
            actual_sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
            if not hmac.compare_digest(actual_sha, blob_sha):
                continue
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if actual_total + len(raw) > self._max_total_bytes:
                continue
            files.append(RepositoryContextFile(path=path, content=content, blob_sha=blob_sha))
            actual_total += len(raw)
        if not files:
            raise RepositoryReadError("no_readable_repository_context_files")
        return RepositorySnapshot(
            repository=repository,
            default_branch=branch,
            commit_sha=commit_sha,
            files=tuple(files),
        )
