from dataclasses import replace

import httpx
import pytest

from app.ai.change_proposals import ProposedFile, validate_proposal
from app.ai.github_repository_writer import GitHubRepositoryWriter, RepositoryWriteError


TOKEN = "ghs_test_installation_token_for_unit_tests"
BASE_SHA = "1" * 40
BASE_TREE = "2" * 40
BLOB_SHA = "3" * 40
TREE_SHA = "4" * 40
COMMIT_SHA = "5" * 40


def _client(handler):
    return httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )


def _successful_handler(calls):
    def handler(request):
        calls.append(request)
        if request.method == "GET" and request.url.path == "/repos/acme/platform":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.method == "GET" and request.url.path == "/repos/acme/platform/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": BASE_SHA}})
        if request.method == "GET" and request.url.path == f"/repos/acme/platform/git/commits/{BASE_SHA}":
            return httpx.Response(200, json={"tree": {"sha": BASE_TREE}})
        if request.method == "POST" and request.url.path == "/repos/acme/platform/git/blobs":
            return httpx.Response(201, json={"sha": BLOB_SHA})
        if request.method == "POST" and request.url.path == "/repos/acme/platform/git/trees":
            return httpx.Response(201, json={"sha": TREE_SHA})
        if request.method == "POST" and request.url.path == "/repos/acme/platform/git/commits":
            return httpx.Response(201, json={"sha": COMMIT_SHA})
        if request.method == "POST" and request.url.path == "/repos/acme/platform/git/refs":
            return httpx.Response(201, json={"ref": "refs/heads/ai/proposal-test"})
        if request.method == "POST" and request.url.path == "/repos/acme/platform/pulls":
            return httpx.Response(
                201,
                json={"number": 42, "html_url": "https://github.com/acme/platform/pull/42"},
            )
        if request.method == "DELETE" and request.url.path.startswith("/repos/acme/platform/git/refs/"):
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "not found"})
    return handler


def test_writer_creates_only_a_new_draft_pr_on_allowlisted_repository():
    calls = []
    client = _client(_successful_handler(calls))
    writer = GitHubRepositoryWriter(
        token=TOKEN,
        allowed_repositories={"acme/platform"},
        client=client,
    )
    proposal = validate_proposal([
        ProposedFile("app/feature.py", "VALUE = 1\n"),
        ProposedFile("docs/guide.md", "proposal notes"),
    ])

    result = writer.create_draft_pull_request(
        proposal,
        repository="acme/platform",
        title="Add reviewed feature",
        summary="Automated candidate for maintainer review.",
    )

    assert result.number == 42
    assert result.url == "https://github.com/acme/platform/pull/42"
    assert result.base_branch == "main"
    assert result.base_sha == BASE_SHA
    assert result.commit_sha == COMMIT_SHA
    assert result.artifact_sha256 == proposal.sha256
    assert result.paths == ("app/feature.py", "docs/guide.md")

    ref_request = next(req for req in calls if req.url.path.endswith("/git/refs"))
    assert ref_request.read()
    assert __import__("json").loads(ref_request.content)["ref"].startswith("refs/heads/ai/proposal-")
    pr_request = next(req for req in calls if req.url.path.endswith("/pulls"))
    pr_payload = __import__("json").loads(pr_request.content)
    assert pr_payload["draft"] is True
    assert pr_payload["base"] == "main"
    assert "master" not in pr_payload["head"]
    assert proposal.sha256 in pr_payload["body"]
    assert pr_request.headers["Authorization"] == f"Bearer {TOKEN}"


def test_writer_rejects_non_allowlisted_repository_before_api_call():
    calls = []
    writer = GitHubRepositoryWriter(
        token=TOKEN,
        allowed_repositories={"acme/platform"},
        client=_client(_successful_handler(calls)),
    )
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])

    with pytest.raises(RepositoryWriteError, match="repository_not_allowlisted"):
        writer.create_draft_pull_request(
            proposal,
            repository="other/repo",
            title="Valid title",
            summary="Summary",
        )

    assert calls == []


def test_writer_rejects_tampered_proposal_before_api_call():
    calls = []
    writer = GitHubRepositoryWriter(
        token=TOKEN,
        allowed_repositories={"acme/platform"},
        client=_client(_successful_handler(calls)),
    )
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    tampered = replace(proposal, artifact=proposal.artifact + b"x")

    with pytest.raises(RepositoryWriteError):
        writer.create_draft_pull_request(
            tampered,
            repository="acme/platform",
            title="Valid title",
            summary="Summary",
        )

    assert calls == []


def test_writer_sanitizes_api_errors_and_does_not_expose_response_body():
    calls = []

    def forbidden(request):
        calls.append(request)
        return httpx.Response(403, json={"message": "secret permission details"})

    writer = GitHubRepositoryWriter(
        token=TOKEN,
        allowed_repositories={"acme/platform"},
        client=_client(forbidden),
    )
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])

    with pytest.raises(RepositoryWriteError, match="github_api_http_403") as error:
        writer.create_draft_pull_request(
            proposal,
            repository="acme/platform",
            title="Valid title",
            summary="Summary",
        )

    assert "secret permission details" not in str(error.value)
    assert calls[0].headers["Authorization"] == f"Bearer {TOKEN}"


def test_writer_attempts_branch_cleanup_when_pr_creation_fails():
    calls = []

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            calls.append(request)
            return httpx.Response(422, json={"message": "pull request rejected"})
        return _successful_handler(calls)(request)

    writer = GitHubRepositoryWriter(
        token=TOKEN,
        allowed_repositories={"acme/platform"},
        client=_client(handler),
    )
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])

    with pytest.raises(RepositoryWriteError, match="github_api_http_422"):
        writer.create_draft_pull_request(
            proposal,
            repository="acme/platform",
            title="Valid title",
            summary="Summary",
        )

    assert any(req.method == "DELETE" for req in calls)


def test_writer_rejects_client_with_non_github_origin():
    client = httpx.Client(base_url="https://attacker.example")
    try:
        with pytest.raises(ValueError, match="official api.github.com"):
            GitHubRepositoryWriter(
                token=TOKEN,
                allowed_repositories={"acme/platform"},
                client=client,
            )
    finally:
        client.close()
