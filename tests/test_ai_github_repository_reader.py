import base64
import hashlib
import json

import httpx
import pytest

from app.ai.github_repository_reader import GitHubRepositoryReader, RepositoryReadError


def _blob(content: bytes):
    sha = hashlib.sha1(
        b"blob " + str(len(content)).encode() + b"\x00" + content,
        usedforsecurity=False,
    ).hexdigest()
    return sha, {
        "sha": sha,
        "size": len(content),
        "encoding": "base64",
        "content": base64.b64encode(content).decode() + "\n",
    }


def _reader(handler, *, allowed=("acme/platform",)):
    client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )
    reader = GitHubRepositoryReader(
        token="ghs_read_only_test_token_123456",
        allowed_repositories=set(allowed),
        client=client,
    )
    return reader, client


def test_reader_loads_bounded_safe_context_and_verifies_git_blob_hashes():
    readme = b"# Platform\nA small service.\n"
    source = b"def endpoint():\n    return 'ok'\n"
    readme_sha, readme_blob = _blob(readme)
    source_sha, source_blob = _blob(source)
    commit_sha = "1" * 40
    tree_sha = "2" * 40
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer ghs_read_only_test_token_123456"
        path = request.url.path
        if path == "/repos/acme/platform":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/acme/platform/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": commit_sha}})
        if path == f"/repos/acme/platform/git/commits/{commit_sha}":
            return httpx.Response(200, json={"tree": {"sha": tree_sha}})
        if path == f"/repos/acme/platform/git/trees/{tree_sha}":
            return httpx.Response(200, json={"truncated": False, "tree": [
                {"path": "README.md", "type": "blob", "sha": readme_sha, "size": len(readme)},
                {"path": "app/service.py", "type": "blob", "sha": source_sha, "size": len(source)},
                {"path": "app/auth/secret.py", "type": "blob", "sha": "3" * 40, "size": 10},
                {"path": ".env", "type": "blob", "sha": "4" * 40, "size": 10},
                {"path": "assets/image.png", "type": "blob", "sha": "5" * 40, "size": 10},
            ]})
        if path == f"/repos/acme/platform/git/blobs/{readme_sha}":
            return httpx.Response(200, json=readme_blob)
        if path == f"/repos/acme/platform/git/blobs/{source_sha}":
            return httpx.Response(200, json=source_blob)
        raise AssertionError(f"Unexpected GitHub API path: {path}")

    reader, client = _reader(handler)
    try:
        snapshot = reader.read_context("acme/platform", goal="fix service endpoint")
        assert snapshot.repository == "acme/platform"
        assert snapshot.default_branch == "main"
        assert snapshot.commit_sha == commit_sha
        assert {item.path for item in snapshot.files} == {"README.md", "app/service.py"}
        assert {item.content for item in snapshot.files} == {
            readme.decode(), source.decode()
        }
        assert all(request.method == "GET" for request in requests)
    finally:
        reader.close()
        client.close()


def test_reader_rejects_non_allowlisted_repository_before_network():
    calls = []
    reader, client = _reader(
        lambda request: (calls.append(request), httpx.Response(200, json={}))[1]
    )
    try:
        with pytest.raises(RepositoryReadError, match="repository_not_allowlisted"):
            reader.read_context("other/repository", goal="inspect")
        assert calls == []
    finally:
        reader.close()
        client.close()


def test_reader_fails_closed_on_truncated_git_tree():
    commit_sha = "1" * 40
    tree_sha = "2" * 40

    def handler(request):
        path = request.url.path
        if path == "/repos/acme/platform":
            return httpx.Response(200, json={"default_branch": "main"})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": commit_sha}})
        if path.endswith(f"/git/commits/{commit_sha}"):
            return httpx.Response(200, json={"tree": {"sha": tree_sha}})
        if path.endswith(f"/git/trees/{tree_sha}"):
            return httpx.Response(200, json={"truncated": True, "tree": []})
        raise AssertionError(path)

    reader, client = _reader(handler)
    try:
        with pytest.raises(RepositoryReadError, match="truncated"):
            reader.read_context("acme/platform", goal="inspect")
    finally:
        reader.close()
        client.close()


def test_reader_rejects_untrusted_api_origin():
    client = httpx.Client(base_url="https://attacker.example")
    try:
        with pytest.raises(ValueError, match="official api.github.com"):
            GitHubRepositoryReader(
                token="ghs_read_only_test_token_123456",
                allowed_repositories={"acme/platform"},
                client=client,
            )
    finally:
        client.close()
