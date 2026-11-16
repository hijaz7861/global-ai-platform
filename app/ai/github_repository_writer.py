"""Least-privilege GitHub writer for opening draft review PRs.

The writer never updates the default branch, merges, deploys, or runs workflows.
Callers must enforce the signed sandbox-attestation and human-approval gate first.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import re
from pathlib import Path
import tempfile
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.ai.change_proposals import ValidatedProposal, materialize_proposal


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_BRANCH = re.compile(r"^ai/proposal-[0-9a-f]{16}$")


class RepositoryWriteError(RuntimeError):
    """A safe, stable error from the restricted GitHub PR writer."""


@dataclass(frozen=True)
class DraftPullRequest:
    repository: str
    number: int
    url: str
    branch: str
    base_branch: str
    base_sha: str
    commit_sha: str
    artifact_sha256: str
    paths: tuple[str, ...]


class GitHubRepositoryWriter:
    """Create one new proposal branch and a draft PR using an injected token."""

    def __init__(
        self,
        *,
        token: str,
        allowed_repositories: Collection[str],
        client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(token, str) or len(token) < 20:
            raise ValueError("A GitHub installation token is required")
        normalized = frozenset(item.strip() for item in allowed_repositories)
        if not normalized or any(not _REPOSITORY.fullmatch(item) for item in normalized):
            raise ValueError("An explicit owner/repository allowlist is required")
        self._allowed = normalized
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "global-ai-platform-governed-writer",
            },
            timeout=httpx.Timeout(20.0),
            follow_redirects=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, path: str, *, payload: dict | None = None) -> dict:
        try:
            response = self._client.request(method, path, json=payload)
        except httpx.HTTPError:
            raise RepositoryWriteError("github_transport_error") from None
        if response.status_code >= 400:
            raise RepositoryWriteError(f"github_api_http_{response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise RepositoryWriteError("github_invalid_json_response") from None
        if not isinstance(data, dict):
            raise RepositoryWriteError("github_invalid_response_shape")
        return data

    def create_draft_pull_request(
        self,
        proposal: ValidatedProposal,
        *,
        repository: str,
        title: str,
        summary: str,
    ) -> DraftPullRequest:
        """Publish a verified proposal on a new branch and open a draft PR.

        The caller is responsible for verifying the signed sandbox attestation
        and action-bound human approval before invoking this method.
        """
        if repository not in self._allowed:
            raise RepositoryWriteError("repository_not_allowlisted")
        if not _REPOSITORY.fullmatch(repository):
            raise RepositoryWriteError("invalid_repository")
        if not isinstance(title, str) or not 5 <= len(title.strip()) <= 120:
            raise RepositoryWriteError("invalid_pull_request_title")
        if not isinstance(summary, str) or len(summary) > 8_000:
            raise RepositoryWriteError("invalid_pull_request_summary")

        owner, repo = repository.split("/", 1)
        api_root = f"/repos/{owner}/{repo}"
        with tempfile.TemporaryDirectory(prefix="gai-github-proposal-") as workspace:
            paths = materialize_proposal(proposal, workspace)
            files = []
            root = Path(workspace)
            for path in paths:
                file_path = root.joinpath(*path.split("/"))
                try:
                    content = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    raise RepositoryWriteError("proposal_workspace_read_failed") from None
                files.append({"path": path, "content": content})

        repo_data = self._request("GET", api_root)
        base_branch = repo_data.get("default_branch")
        if not isinstance(base_branch, str) or not re.fullmatch(
            r"[A-Za-z0-9._/-]{1,200}", base_branch
        ):
            raise RepositoryWriteError("invalid_default_branch")
        encoded_branch = quote(base_branch, safe="")
        ref_data = self._request("GET", f"{api_root}/git/ref/heads/{encoded_branch}")
        base_sha = (ref_data.get("object") or {}).get("sha")
        if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
            raise RepositoryWriteError("invalid_base_commit")
        commit_data = self._request("GET", f"{api_root}/git/commits/{base_sha}")
        base_tree = (commit_data.get("tree") or {}).get("sha")
        if not isinstance(base_tree, str) or not re.fullmatch(r"[0-9a-f]{40}", base_tree):
            raise RepositoryWriteError("invalid_base_tree")

        tree_entries = []
        for item in files:
            blob = self._request(
                "POST",
                f"{api_root}/git/blobs",
                payload={"content": item["content"], "encoding": "utf-8"},
            )
            blob_sha = blob.get("sha")
            if not isinstance(blob_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", blob_sha):
                raise RepositoryWriteError("invalid_blob_sha")
            tree_entries.append({
                "path": item["path"],
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            })

        tree = self._request(
            "POST",
            f"{api_root}/git/trees",
            payload={"base_tree": base_tree, "tree": tree_entries},
        )
        tree_sha = tree.get("sha")
        if not isinstance(tree_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", tree_sha):
            raise RepositoryWriteError("invalid_tree_sha")

        commit = self._request(
            "POST",
            f"{api_root}/git/commits",
            payload={
                "message": f"ai: propose {proposal.sha256[:12]}",
                "tree": tree_sha,
                "parents": [base_sha],
            },
        )
        commit_sha = commit.get("sha")
        if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
            raise RepositoryWriteError("invalid_commit_sha")

        branch = "ai/proposal-" + uuid4().hex[:16]
        if not _BRANCH.fullmatch(branch):
            raise RepositoryWriteError("invalid_proposal_branch")
        self._request(
            "POST",
            f"{api_root}/git/refs",
            payload={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )

        body = (
            f"{summary.strip()}\n\n"
            f"---\n"
            f"**Proposal SHA-256:** `{proposal.sha256}`\n"
            f"**Risk classification:** `{proposal.risk_level}`\n"
            f"**Files:** {len(paths)}\n"
            f"**Review mode:** Draft only; no automatic merge or deployment.\n"
        )
        try:
            pull = self._request(
                "POST",
                f"{api_root}/pulls",
                payload={
                    "title": title.strip(),
                    "head": branch,
                    "base": base_branch,
                    "body": body,
                    "draft": True,
                    "maintainer_can_modify": False,
                },
            )
        except RepositoryWriteError:
            # Best-effort cleanup; failure is deliberately not reported as success.
            try:
                self._request("DELETE", f"{api_root}/git/refs/heads/{quote(branch, safe='/')}")
            except RepositoryWriteError:
                pass
            raise

        number = pull.get("number")
        url = pull.get("html_url")
        if not isinstance(number, int) or not isinstance(url, str) or not url.startswith("https://github.com/"):
            raise RepositoryWriteError("invalid_pull_request_response")
        return DraftPullRequest(
            repository=repository,
            number=number,
            url=url,
            branch=branch,
            base_branch=base_branch,
            base_sha=base_sha,
            commit_sha=commit_sha,
            artifact_sha256=proposal.sha256,
            paths=paths,
        )
