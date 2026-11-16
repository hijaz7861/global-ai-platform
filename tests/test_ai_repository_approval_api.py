from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.ai.nonce_store import PostgresNonceStore
from app.api.routes import ai_repository
from app.api.routes.ai_repository import (
    CreateApprovedRepositoryPRRequest,
    SandboxAttestationInput,
    ProposedFileInput,
)
from app.ai.change_proposals import ProposedFile, validate_proposal
from app.ai.github_repository_writer import DraftPullRequest
from app.models import Membership, User


class FakeResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class FakePostgresSession:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self.rowcount)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_postgres_nonce_store_uses_primary_key_conflict_guard():
    db = FakePostgresSession(rowcount=1)
    store = PostgresNonceStore(db)

    assert store.consume_nonce("nonce-value-00000001") is True
    assert len(db.statements) == 2
    compiled = str(db.statements[1].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (nonce) DO NOTHING" in compiled


def test_postgres_nonce_store_returns_false_for_replay():
    db = FakePostgresSession(rowcount=0)
    store = PostgresNonceStore(db)

    assert store.consume_nonce("nonce-value-00000001") is False


def test_postgres_nonce_store_fails_closed_on_sqlite():
    db = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    )
    with pytest.raises(RuntimeError, match="PostgreSQL nonce storage"):
        PostgresNonceStore(db)


def test_approval_endpoint_signs_for_authenticated_actor_and_commits_before_token(monkeypatch):
    files = [ProposedFile("docs/approval-flow.md", "reviewed change")]
    proposal = validate_proposal(files)
    user = User(
        id="authenticated-user-001",
        email="owner@example.test",
        password_hash="unused-test-hash",
        is_active=True,
        email_verified=True,
        mfa_enabled=True,
    )
    membership = Membership(
        user_id=user.id,
        organization_id="org-001",
        role="owner",
    )
    db = FakePostgresSession()
    settings = ai_repository.settings
    monkeypatch.setattr(settings, "ai_repository_writes_enabled", True)
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://test")
    monkeypatch.setattr(settings, "github_repository_allowlist", "hijaz7861/global-ai-platform")
    monkeypatch.setattr(settings, "github_app_id", "12345")
    monkeypatch.setattr(settings, "github_app_installation_id", "67890")
    monkeypatch.setattr(settings, "github_app_private_key_pem", SimpleNamespace(
        get_secret_value=lambda: "test-private-key"
    ))
    monkeypatch.setattr(settings, "ai_sandbox_attestation_hmac_key", SimpleNamespace(
        get_secret_value=lambda: "a" * 32
    ))
    monkeypatch.setattr(settings, "ai_human_approval_hmac_key", SimpleNamespace(
        get_secret_value=lambda: "b" * 32
    ))

    class FakeProvider:
        allowed_repositories = frozenset({"hijaz7861/global-ai-platform"})

        def __init__(self, **kwargs):
            self.closed = False

        def get_token(self, repository):
            return "ghs_fake_installation_token_for_unit_tests"

        def close(self):
            self.closed = True

    class FakeNonceStore:
        def __init__(self, session):
            self.session = session

        def consume_nonce(self, nonce):
            return True

    captured = {}

    def fake_create(proposal_arg, **kwargs):
        captured["proposal"] = proposal_arg
        captured.update(kwargs)
        # Simulate the commit boundary before token retrieval.
        kwargs["token_provider"].get_token(kwargs["repository"])
        return DraftPullRequest(
            repository=kwargs["repository"],
            number=77,
            url="https://github.com/hijaz7861/global-ai-platform/pull/77",
            branch="ai/proposal-0123456789abcdef",
            base_branch="master",
            base_sha="1" * 40,
            commit_sha="2" * 40,
            artifact_sha256=proposal_arg.sha256,
            paths=proposal_arg.paths,
        )

    monkeypatch.setattr(ai_repository, "GitHubAppTokenProvider", FakeProvider)
    monkeypatch.setattr(ai_repository, "PostgresNonceStore", FakeNonceStore)
    monkeypatch.setattr(ai_repository, "create_approved_draft_pull_request_with_app", fake_create)

    payload = CreateApprovedRepositoryPRRequest(
        repository="hijaz7861/global-ai-platform",
        title="Review governed runtime",
        summary="A candidate change for review.",
        files=[ProposedFileInput(path=files[0].path, content=files[0].content)],
        attestation=SandboxAttestationInput(
            artifact_sha256=proposal.sha256,
            run_id="sandbox-run-0001",
            sandbox_id="isolated-sandbox-01",
            tests_passed=True,
            security_scan_passed=True,
            regression_suite_passed=True,
            issued_at=1_800_000_000,
            nonce="sandbox-nonce-00000001",
            signature_hex="c" * 64,
        ),
    )

    result = ai_repository.create_human_approved_draft_pr(
        "org-001", payload, membership, user, db
    )

    assert result.number == 77
    assert captured["signed_approval"].payload.actor_id == user.id
    assert captured["signed_approval"].payload.action == "repository.pull_request.create"
    assert captured["signed_approval"].payload.action_sha256 == proposal.sha256
    assert captured["allowed_actor_ids"] == {user.id}
    assert db.commits == 1
    assert db.rollbacks == 0


def test_approval_endpoint_rejects_without_mfa_before_provider_creation(monkeypatch):
    user = User(
        id="authenticated-user-002",
        email="owner@example.test",
        password_hash="unused-test-hash",
        is_active=True,
        email_verified=True,
        mfa_enabled=False,
    )
    membership = Membership(
        user_id=user.id,
        organization_id="org-001",
        role="owner",
    )
    db = FakePostgresSession()
    called = []
    monkeypatch.setattr(
        ai_repository,
        "GitHubAppTokenProvider",
        lambda **kwargs: called.append(kwargs),
    )
    payload = CreateApprovedRepositoryPRRequest(
        repository="hijaz7861/global-ai-platform",
        title="Review governed runtime",
        summary="A candidate change for review.",
        files=[ProposedFileInput(path="docs/approval.md", content="change")],
        attestation=SandboxAttestationInput(
            artifact_sha256="d" * 64,
            run_id="sandbox-run-0001",
            sandbox_id="isolated-sandbox-01",
            tests_passed=True,
            security_scan_passed=True,
            regression_suite_passed=True,
            issued_at=1_800_000_000,
            nonce="sandbox-nonce-00000002",
            signature_hex="c" * 64,
        ),
    )

    with pytest.raises(Exception) as error:
        ai_repository.create_human_approved_draft_pr(
            "org-001", payload, membership, user, db
        )
    assert getattr(error.value, "status_code", None) == 403
    assert called == []
