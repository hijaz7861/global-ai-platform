"""Combine signed sandbox evidence and signed human approval for staged releases.

This is a policy composition layer only. It does not execute code, sign approvals,
write to GitHub, or deploy. All nonce callbacks must be durable and atomic.
"""
from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import replace
from hashlib import sha256
import hmac

from app.ai.autonomy_gate import (
    ChangeEvidence,
    GateDecision,
    GateResult,
    evaluate_change,
)
from app.ai.evidence_attestation import SignedAttestation, verify_attestation
from app.ai.human_approval import SignedHumanApproval
from app.ai.release_gate import evaluate_change_with_approval


def evaluate_governed_change(
    evidence: ChangeEvidence,
    *,
    signed_attestation: SignedAttestation | None,
    attestation_key: bytes,
    consume_attestation_nonce: Callable[[str], bool],
    signed_approval: SignedHumanApproval | None,
    approval_key: bytes,
    allowed_actor_ids: Collection[str],
    consume_approval_nonce: Callable[[str], bool],
    now: int | None = None,
    attestation_max_age_seconds: int = 900,
    attestation_future_skew_seconds: int = 30,
    approval_max_lifetime_seconds: int = 900,
    approval_future_skew_seconds: int = 30,
) -> GateResult:
    """Require digest-bound sandbox attestation and, when needed, human approval.

    First verify the attestation without consuming its nonce. This allows the
    caller to obtain a REQUIRE_HUMAN_APPROVAL decision without burning a valid
    sandbox attestation. Invalid proposals and invalid approvals also fail before
    either nonce is consumed. Once all policy checks pass, consume the attestation
    nonce and then the human-approval nonce through their durable callbacks.
    """
    actual_digest = sha256(evidence.artifact).hexdigest()
    if not evidence.claimed_sha256 or not hmac.compare_digest(
        actual_digest, evidence.claimed_sha256.lower()
    ):
        return GateResult(GateDecision.DENY, ("sha256_mismatch",), actual_digest)
    if signed_attestation is None:
        return GateResult(GateDecision.DENY, ("sandbox_attestation_missing",), actual_digest)

    valid, reason = verify_attestation(
        signed_attestation,
        key=attestation_key,
        expected_artifact_sha256=actual_digest,
        consume_nonce=lambda _nonce: True,
        now=now,
        max_age_seconds=attestation_max_age_seconds,
        max_future_skew_seconds=attestation_future_skew_seconds,
    )
    if not valid:
        return GateResult(
            GateDecision.DENY,
            (f"sandbox_attestation_{reason}",),
            actual_digest,
        )

    attested_evidence = replace(
        evidence,
        claimed_sha256=actual_digest,
        sandbox_isolated=True,
        tests_passed=signed_attestation.payload.tests_passed,
        security_scan_passed=signed_attestation.payload.security_scan_passed,
        regression_suite_passed=signed_attestation.payload.regression_suite_passed,
        human_approval=False,
    )
    preliminary = evaluate_change(attested_evidence)
    if preliminary.decision is GateDecision.DENY:
        return preliminary

    needs_approval = (
        evidence.risk.value in {"high", "critical"}
        or evidence.production_release_requested
    )
    if needs_approval and signed_approval is None:
        return preliminary

    if signed_approval is not None:
        # Validate the human signature and policy before consuming either nonce.
        approval_preflight = evaluate_change_with_approval(
            attested_evidence,
            signed_approval=signed_approval,
            approval_key=approval_key,
            allowed_actor_ids=allowed_actor_ids,
            consume_nonce=lambda _nonce: True,
            now=now,
            max_lifetime_seconds=approval_max_lifetime_seconds,
            max_future_skew_seconds=approval_future_skew_seconds,
        )
        if approval_preflight.decision is GateDecision.DENY:
            return approval_preflight
    elif needs_approval:
        return preliminary

    valid, reason = verify_attestation(
        signed_attestation,
        key=attestation_key,
        expected_artifact_sha256=actual_digest,
        consume_nonce=consume_attestation_nonce,
        now=now,
        max_age_seconds=attestation_max_age_seconds,
        max_future_skew_seconds=attestation_future_skew_seconds,
    )
    if not valid:
        return GateResult(
            GateDecision.DENY,
            (f"sandbox_attestation_{reason}",),
            actual_digest,
        )

    return evaluate_change_with_approval(
        attested_evidence,
        signed_approval=signed_approval,
        approval_key=approval_key,
        allowed_actor_ids=allowed_actor_ids,
        consume_nonce=consume_approval_nonce,
        now=now,
        max_lifetime_seconds=approval_max_lifetime_seconds,
        max_future_skew_seconds=approval_future_skew_seconds,
    )
