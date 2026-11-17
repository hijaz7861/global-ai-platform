import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.ai.change_proposals import ProposedFile, validate_proposal
from app.ai.github_repository_reader import RepositoryContextFile, RepositorySnapshot
from app.ai.providers import ProviderError
from app.ai.runtime.tasks import PermanentTaskError
from app.ai import proposal_generation_task as handler


@dataclass
class FakeMembership:
    role: str = "owner"


class FakeDB:
    def __init__(self):
        self.commits = 0

    def scalar(self, statement):
        return FakeMembership()

    def commit(self):
        self.commits += 1


class FakeContext:
    def __init__(self, repository="acme/platform", goal="Add a safe status endpoint"):
        self.db = FakeDB()
        self.task = SimpleNamespace(
            id="task-generate-0001",
            organization_id="org-0001",
            created_by_user_id="user-0001",
            input_payload={
                "repository": repository,
                "goal": goal,
                "model_route": "primary",
                "max_output_tokens": 512,
            },
        )
        self.heartbeats = 0

    def heartbeat(self):
        self.heartbeats += 1


class FakeProvider:
    allowed_repositories = frozenset({"acme/platform"})

    def __init__(self):
        self.read_calls = []
        self.closed = False

    def get_read_token(self, repository):
        self.read_calls.append(repository)
        return "ghs_contents_read_only_token"

    def close(self):
        self.closed = True


class FakeReader:
    def __init__(self, token, allowed_repositories):
        assert token == "ghs_contents_read_only_token"
        assert allowed_repositories == ("acme/platform",)
        self.closed = False

    def read_context(self, repository, *, goal):
        assert repository == "acme/platform"
        assert goal == "Add a safe status endpoint"
        return RepositorySnapshot(
            repository=repository,
            default_branch="main",
            commit_sha="1" * 40,
            files=(
                RepositoryContextFile(
                    path="README.md",
                    content="# Existing service",
                    blob_sha="2" * 40,
                ),
            ),
        )

    def close(self):
        self.closed = True


class FakeRouter:
    def __init__(self, content=None, error=None):
        self.content = content or json.dumps({
            "title": "Add safe status endpoint",
            "summary": "Adds a minimal read-only status document.",
            "files": [{
                "path": "docs/status-endpoint.md",
                "content": "# Status endpoint\\nRead-only status contract.\\n",
                "mode": "100644",
            }],
        })
        self.error = error
        self.prompt = None
        self.selected = []

    def select(self, **kwargs):
        self.selected.append(kwargs)

    async def complete(self, **kwargs):
        self.prompt = kwargs["prompt"]
        if self.error:
            raise self.error
        return {
            "provider": "test-provider",
            "model": "test-model",
            "content": self.content,
            "input_tokens": 100,
            "output_tokens": 50,
        }


def _configure(monkeypatch, *, content=None, error=None):
    provider = FakeProvider()
    reader_instances = []
    router = FakeRouter(content=content, error=error)
    monkeypatch.setattr(handler, "_runtime_provider", lambda: (provider, ("acme/platform",)))

    def reader_factory(**kwargs):
        reader = FakeReader(**kwargs)
        reader_instances.append(reader)
        return reader

    monkeypatch.setattr(handler, "GitHubRepositoryReader", reader_factory)
    monkeypatch.setattr(handler, "create_model_router", lambda settings: router)
    reserved = []
    finished = []
    failed = []
    audits = []
    monkeypatch.setattr(handler, "_reserve_model_call", lambda *a, **kw: reserved.append(kw) or "request-001")
    monkeypatch.setattr(handler, "_finish_model_call", lambda *a, **kw: finished.append(kw))
    monkeypatch.setattr(handler, "_fail_model_call", lambda *a, **kw: failed.append(kw))
    monkeypatch.setattr(handler, "record_audit_event", lambda *a, **kw: audits.append(kw))
    return provider, reader_instances, router, reserved, finished, failed, audits


def test_generation_reads_allowlisted_repo_and_returns_validated_proposal(monkeypatch):
    context = FakeContext()
    provider, readers, router, reserved, finished, failed, audits = _configure(monkeypatch)

    result = handler.run_proposal_generation_task(context)

    expected = validate_proposal([
        ProposedFile(
            path="docs/status-endpoint.md",
            content="# Status endpoint\\nRead-only status contract.\\n",
        )
    ])
    assert result["status"] == "proposal_ready"
    assert result["repository"] == "acme/platform"
    assert result["base_branch"] == "main"
    assert result["base_sha"] == "1" * 40
    assert result["artifact_sha256"] == expected.sha256
    assert result["paths"] == ["docs/status-endpoint.md"]
    assert result["provider"] == "test-provider"
    assert provider.read_calls == ["acme/platform"]
    assert provider.closed is True
    assert readers[0].closed is True
    assert len(reserved) == 1 and len(finished) == 1 and failed == []
    assert context.heartbeats == 3
    assert context.db.commits == 1
    assert audits[0]["action"] == "ai.proposal.generated"
    assert "untrusted data" in router.prompt


def test_generation_rejects_repository_outside_allowlist_before_read(monkeypatch):
    context = FakeContext(repository="other/repository")
    provider, readers, *_ = _configure(monkeypatch)

    with pytest.raises(PermanentTaskError, match="repository_not_allowlisted"):
        handler.run_proposal_generation_task(context)

    assert provider.read_calls == []
    assert provider.closed is True
    assert readers == []


def test_generation_rejects_sensitive_generated_paths(monkeypatch):
    content = json.dumps({
        "title": "Modify authentication settings",
        "summary": "Unsafe path should be blocked.",
        "files": [{"path": "app/auth/session.py", "content": "unsafe"}],
    })
    context = FakeContext()
    _, _, _, _, finished, failed, audits = _configure(monkeypatch, content=content)

    with pytest.raises(PermanentTaskError, match="model_proposal_failed_validation"):
        handler.run_proposal_generation_task(context)

    assert len(finished) == 1
    assert failed == []
    assert audits == []


def test_generation_records_provider_failure_and_does_not_create_proposal(monkeypatch):
    context = FakeContext()
    _, _, _, reserved, finished, failed, audits = _configure(
        monkeypatch, error=ProviderError("provider unavailable", retryable=True)
    )

    with pytest.raises(PermanentTaskError, match="proposal_model_provider_failed"):
        handler.run_proposal_generation_task(context)

    assert len(reserved) == 1
    assert finished == []
    assert len(failed) == 1
    assert audits == []


def test_generation_rejects_non_owner_membership_before_external_calls(monkeypatch):
    context = FakeContext()
    context.db.scalar = lambda statement: FakeMembership(role="member")
    called = []
    monkeypatch.setattr(handler, "_runtime_provider", lambda: called.append(True))

    with pytest.raises(PermanentTaskError, match="membership_revoked"):
        handler.run_proposal_generation_task(context)

    assert called == []
