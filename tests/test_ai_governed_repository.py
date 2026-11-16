import pytest

from app.ai.autonomy_gate import GateDecision
from app.ai.change_proposals import ProposedFile, ValidatedProposal, validate_proposal
from app.ai.evidence_attestation import SandboxAttestation, sign_attestation
from app.ai.governed_repository import (
    RepositoryWriteDenied,
    create_approved_draft_pull_request,
)
from app.ai.github_repository_writer import DraftPullRequest, RepositoryWriteError
from app.ai.human_approval import HumanApproval, sign_human_approval


ATTESTATION_KEY = b"test-attestation-key-with-at-least-32-bytes"
APPROVAL_KEY = b"test-approval-key-with-at-least-32-bytes"
NOW = 1_800_000_000
ACTION = "repository.pull_request.create"


class FakeWriter:
    def __init__(self):
        self.calls = []

    def create_draft_pull_request(self, proposal, **kwargs):
        self.calls.append((proposal, kwargs))
        return DraftPullRequest(
            repository=kwargs["repository"],
            number=12,
            url="https://github.com/acme/platform/pull/12",
            branch="ai/proposal-0123456789abcdef",
            base_branch="main",
            base_sha="1" * 40,
            commit_sha="2" * 40,
            artifact_sha256=proposal.sha256,
            paths=proposal.paths,
        )


def _attestation(proposal):
    payload = SandboxAttestation(
        artifact_sha256=proposal.sha256,
        run_id="run-0001",
        sandbox_id="sandbox-0001",
        tests_passed=True,
        security_scan_passed=True,
        regression_suite_passed=True,
        issued_at=NOW,
        nonce="attestation-nonce-0001",
    )
    return sign_attestation(payload, ATTESTATION_KEY)


def _approval(proposal, *, action=ACTION, actor="owner-1"):
    payload = HumanApproval(
        action_sha256=proposal.sha256,
        actor_id=actor,
        approval_id="approval-0001",
        issued_at=NOW,
        expires_at=NOW + 300,
        nonce="human-approval-nonce-01",
        action=action,
    )
    return sign_human_approval(payload, APPROVAL_KEY)


def _nonce_consumer():
    used = set()

    def consume(nonce):
        if nonce in used:
            return False
        used.add(nonce)
        return True

    return used, consume


def _invoke(proposal, writer, attestation, approval, consume_attestation, consume_approval):
    return create_approved_draft_pull_request(
        proposal,
        writer=writer,
        repository="acme/platform",
        title="Propose reviewed change",
        summary="Candidate changes for maintainer review.",
        signed_attestation=attestation,
        attestation_key=ATTESTATION_KEY,
        consume_attestation_nonce=consume_attestation,
        signed_approval=approval,
        approval_key=APPROVAL_KEY,
        allowed_actor_ids={"owner-1"},
        consume_approval_nonce=consume_approval,
        now=NOW,
    )


def test_draft_pr_is_created_only_after_signed_attestation_and_action_approval():
    proposal = validate_proposal([ProposedFile("app/feature.py", "VALUE = 1\n")])
    writer = FakeWriter()
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    result = _invoke(
        proposal,
        writer,
        _attestation(proposal),
        _approval(proposal),
        consume_attestation,
        consume_approval,
    )

    assert result.number == 12
    assert result.artifact_sha256 == proposal.sha256
    assert len(writer.calls) == 1
    assert writer.calls[0][1]["repository"] == "acme/platform"
    assert attestation_nonces == {"attestation-nonce-0001"}
    assert approval_nonces == {"human-approval-nonce-01"}


def test_missing_approval_does_not_write_or_consume_attestation_nonce():
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    writer = FakeWriter()
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    with pytest.raises(RepositoryWriteDenied) as error:
        _invoke(
            proposal,
            writer,
            _attestation(proposal),
            None,
            consume_attestation,
            consume_approval,
        )

    assert error.value.decision is GateDecision.REQUIRE_HUMAN_APPROVAL
    assert writer.calls == []
    assert attestation_nonces == set()
    assert approval_nonces == set()


def test_wrong_approval_action_is_denied_without_writing():
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    writer = FakeWriter()
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    with pytest.raises(RepositoryWriteDenied) as error:
        _invoke(
            proposal,
            writer,
            _attestation(proposal),
            _approval(proposal, action="production_release"),
            consume_attestation,
            consume_approval,
        )

    assert error.value.decision is GateDecision.DENY
    assert writer.calls == []
    assert attestation_nonces == set()
    assert approval_nonces == set()


def test_unauthorized_approver_is_denied_without_writing():
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    writer = FakeWriter()
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    with pytest.raises(RepositoryWriteDenied):
        _invoke(
            proposal,
            writer,
            _attestation(proposal),
            _approval(proposal, actor="other-user"),
            consume_attestation,
            consume_approval,
        )

    assert writer.calls == []
    assert attestation_nonces == set()
    assert approval_nonces == set()


def test_invalid_proposal_is_rejected_before_nonce_consumption():
    proposal = validate_proposal([ProposedFile("docs/a.md", "safe")])
    tampered = ValidatedProposal(
        artifact=proposal.artifact + b"x",
        sha256=proposal.sha256,
        paths=proposal.paths,
        risk_level=proposal.risk_level,
        total_bytes=proposal.total_bytes,
    )
    writer = FakeWriter()
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    with pytest.raises(RepositoryWriteError, match="invalid_proposal_artifact"):
        _invoke(
            tampered,
            writer,
            _attestation(proposal),
            _approval(proposal),
            consume_attestation,
            consume_approval,
        )

    assert writer.calls == []
    assert attestation_nonces == set()
    assert approval_nonces == set()
