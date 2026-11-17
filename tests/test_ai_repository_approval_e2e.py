import time

from pydantic import SecretStr

from app.ai.change_proposals import ProposedFile, validate_proposal
from app.ai.evidence_attestation import SandboxAttestation, sign_attestation
from app.ai.github_repository_writer import DraftPullRequest
from app.api.routes import ai_repository
from app.api.routes.ai_repository import (
    CreateApprovedRepositoryPRRequest,
    ProposedFileInput,
    SandboxAttestationInput,
)
from app.models import Membership, User


def test_full_approval_gate_reaches_mocked_draft_writer_only_after_valid_proofs(monkeypatch):
    repository = "hijaz7861/global-ai-platform"
    files = [ProposedFile("docs/e2e-approved-change.md", "verified candidate")]
    proposal = validate_proposal(files)
    now = int(time.time())
    attestation_key = b"a" * 32
    approval_key = b"b" * 32
    attestation = SandboxAttestation(
        artifact_sha256=proposal.sha256,
        run_id="sandbox-run-e2e-0001",
        sandbox_id="isolated-sandbox-e2e",
        tests_passed=True,
        security_scan_passed=True,
        regression_suite_passed=True,
        issued_at=now - 1,
        nonce="sandbox-nonce-e2e-00000001",
        repository="hijaz7861/global-ai-platform",
        base_sha="1" * 40,
    )
    signed_attestation = sign_attestation(attestation, attestation_key)

    user = User(
        id="authenticated-owner-e2e-001",
        email="owner@example.test",
        password_hash="unused-test-hash",
        is_active=True,
        email_verified=True,
        mfa_enabled=True,
    )
    membership = Membership(
        user_id=user.id,
        organization_id="org-e2e-001",
        role="owner",
    )

    class FakeDB:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.audit_events = []

        def add(self, item):
            if item not in self.audit_events:
                self.audit_events.append(item)

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    db = FakeDB()
    settings = ai_repository.settings
    monkeypatch.setattr(settings, "ai_repository_writes_enabled", True)
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://test")
    monkeypatch.setattr(settings, "github_repository_allowlist", repository)
    monkeypatch.setattr(settings, "github_app_id", "12345")
    monkeypatch.setattr(settings, "github_app_installation_id", "67890")
    monkeypatch.setattr(settings, "github_app_private_key_pem", SecretStr("unused-mocked-key"))
    monkeypatch.setattr(settings, "ai_sandbox_attestation_hmac_key", SecretStr(attestation_key.decode()))
    monkeypatch.setattr(settings, "ai_human_approval_hmac_key", SecretStr(approval_key.decode()))

    nonce_calls = []

    class FakeNonceStore:
        def __init__(self, session):
            self.seen = set()

        def consume_nonce(self, nonce):
            nonce_calls.append(nonce)
            if nonce in self.seen:
                return False
            self.seen.add(nonce)
            return True

    provider_state = {"token_requests": [], "closed": False}

    class FakeProvider:
        allowed_repositories = frozenset({repository})

        def __init__(self, **kwargs):
            pass

        def get_token(self, target_repository):
            provider_state["token_requests"].append(target_repository)
            return "ghs_fake_installation_token_for_e2e"

        def close(self):
            provider_state["closed"] = True

    writer_state = {"created": False, "calls": []}

    class FakeWriter:
        def __init__(self, *, token, allowed_repositories):
            assert token == "ghs_fake_installation_token_for_e2e"
            assert allowed_repositories == {repository} or allowed_repositories == frozenset({repository})
            writer_state["created"] = True

        def create_draft_pull_request(self, proposal_arg, **kwargs):
            writer_state["calls"].append(kwargs)
            return DraftPullRequest(
                repository=kwargs["repository"],
                number=88,
                url="https://github.com/hijaz7861/global-ai-platform/pull/88",
                branch="ai/proposal-abcdef0123456789",
                base_branch="master",
                base_sha="1" * 40,
                commit_sha="2" * 40,
                artifact_sha256=proposal_arg.sha256,
                paths=proposal_arg.paths,
            )

        def close(self):
            pass

    import app.ai.governed_repository as governed_repository

    monkeypatch.setattr(ai_repository, "PostgresNonceStore", FakeNonceStore)
    monkeypatch.setattr(ai_repository, "GitHubAppTokenProvider", FakeProvider)
    monkeypatch.setattr(governed_repository, "GitHubRepositoryWriter", FakeWriter)

    payload = CreateApprovedRepositoryPRRequest(
        repository=repository,
        title="Approve verified runtime change",
        summary="Candidate passed sandbox validation and human approval.",
        files=[ProposedFileInput(path=files[0].path, content=files[0].content)],
        attestation=SandboxAttestationInput(
            artifact_sha256=proposal.sha256,
            repository=attestation.repository,
            base_sha=attestation.base_sha,
            run_id=attestation.run_id,
            sandbox_id=attestation.sandbox_id,
            tests_passed=True,
            security_scan_passed=True,
            regression_suite_passed=True,
            issued_at=attestation.issued_at,
            nonce=attestation.nonce,
            signature_hex=signed_attestation.signature_hex,
        ),
    )

    result = ai_repository.create_human_approved_draft_pr(
        "org-e2e-001", payload, membership, user, db
    )

    assert result.number == 88
    assert result.review_mode == "draft_only"
    assert len(nonce_calls) == 2
    assert nonce_calls[0] == attestation.nonce
    assert nonce_calls[0] != nonce_calls[1]
    assert db.commits == 2
    assert len(db.audit_events) == 1
    assert db.audit_events[0].details["status"] == "draft_pr_created"
    assert db.audit_events[0].details["pull_request_number"] == 88
    assert provider_state["token_requests"] == [repository]
    assert provider_state["closed"] is True
    assert writer_state["created"] is True
    assert len(writer_state["calls"]) == 1
