"""Human-approved, attested GitHub draft-PR orchestration."""
from __future__ import annotations

from collections.abc import Callable, Collection
import tempfile

from app.ai.autonomy_gate import ChangeEvidence, GateDecision, RiskLevel
from app.ai.change_proposals import ProposalValidationError, ValidatedProposal, materialize_proposal
from app.ai.evidence_attestation import SignedAttestation
from app.ai.governed_release import evaluate_governed_change
from app.ai.github_app_tokens import GitHubAppTokenProvider
from app.ai.github_repository_writer import (
    DraftPullRequest,
    GitHubRepositoryWriter,
    RepositoryWriteError,
)
from app.ai.human_approval import SignedHumanApproval


_REPOSITORY_PR_ACTION = "repository.pull_request.create"


class RepositoryWriteDenied(RepositoryWriteError):
    """The signed evidence/approval gate did not authorize draft PR creation."""

    def __init__(self, decision: GateDecision, reasons: tuple[str, ...]) -> None:
        self.decision = decision
        self.reasons = reasons
        super().__init__(f"repository_write_denied:{decision.value}")


def create_approved_draft_pull_request(
    proposal: ValidatedProposal,
    *,
    writer: GitHubRepositoryWriter,
    repository: str,
    title: str,
    summary: str,
    signed_attestation: SignedAttestation,
    attestation_key: bytes,
    consume_attestation_nonce: Callable[[str], bool],
    signed_approval: SignedHumanApproval | None,
    approval_key: bytes,
    allowed_actor_ids: Collection[str],
    consume_approval_nonce: Callable[[str], bool],
    now: int | None = None,
) -> DraftPullRequest:
    """Create a draft PR only after digest-bound evidence and human approval.

    Every repository write is treated as high impact, regardless of the proposal's
    content risk classification. The approval action is specifically bound to
    repository.pull_request.create; it does not authorize merge or deployment.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="gai-approved-proposal-") as workspace:
            materialize_proposal(proposal, workspace)
    except ProposalValidationError:
        raise RepositoryWriteError("invalid_proposal_artifact") from None

    evidence = ChangeEvidence(
        artifact=proposal.artifact,
        claimed_sha256=proposal.sha256,
        sandbox_isolated=False,
        tests_passed=False,
        security_scan_passed=False,
        regression_suite_passed=False,
        rollback_ready=True,
        risk=RiskLevel.HIGH,
        human_approval=False,
        production_release_requested=False,
        action=_REPOSITORY_PR_ACTION,
    )
    gate = evaluate_governed_change(
        evidence,
        signed_attestation=signed_attestation,
        attestation_key=attestation_key,
        consume_attestation_nonce=consume_attestation_nonce,
        signed_approval=signed_approval,
        approval_key=approval_key,
        allowed_actor_ids=allowed_actor_ids,
        consume_approval_nonce=consume_approval_nonce,
        now=now,
    )
    # SANDBOX_ONLY here means review-only PR creation, not merge or deployment.
    if gate.decision is not GateDecision.SANDBOX_ONLY or signed_approval is None:
        raise RepositoryWriteDenied(gate.decision, gate.reasons)

    return writer.create_draft_pull_request(
        proposal,
        repository=repository,
        title=title,
        summary=summary,
    )



def create_approved_draft_pull_request_with_app(
    proposal: ValidatedProposal,
    *,
    token_provider: GitHubAppTokenProvider,
    repository: str,
    title: str,
    summary: str,
    signed_attestation: SignedAttestation,
    attestation_key: bytes,
    consume_attestation_nonce: Callable[[str], bool],
    signed_approval: SignedHumanApproval | None,
    approval_key: bytes,
    allowed_actor_ids: Collection[str],
    consume_approval_nonce: Callable[[str], bool],
    now: int | None = None,
) -> DraftPullRequest:
    """Use a scoped GitHub App token, requesting it only after approval succeeds."""
    if repository not in token_provider.allowed_repositories:
        raise RepositoryWriteError("repository_not_allowlisted")

    class _LazyAppWriter:
        def create_draft_pull_request(self, proposal, **kwargs):
            token = token_provider.get_token(repository)
            writer = GitHubRepositoryWriter(
                token=token,
                allowed_repositories=token_provider.allowed_repositories,
            )
            try:
                return writer.create_draft_pull_request(proposal, **kwargs)
            finally:
                writer.close()

    return create_approved_draft_pull_request(
        proposal,
        writer=_LazyAppWriter(),
        repository=repository,
        title=title,
        summary=summary,
        signed_attestation=signed_attestation,
        attestation_key=attestation_key,
        consume_attestation_nonce=consume_attestation_nonce,
        signed_approval=signed_approval,
        approval_key=approval_key,
        allowed_actor_ids=allowed_actor_ids,
        consume_approval_nonce=consume_approval_nonce,
        now=now,
    )
