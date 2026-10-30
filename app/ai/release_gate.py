"""Signed-approval integration for the AI autonomy gate.

This wrapper ignores the legacy human_approval boolean and verifies an
action-bound signed envelope before staged-release eligibility is returned.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace

from app.ai.autonomy_gate import (
    ChangeEvidence,
    GateDecision,
    GateResult,
    RiskLevel,
    evaluate_change,
)
from app.ai.human_approval import SignedHumanApproval, verify_human_approval


def evaluate_change_with_approval(
    evidence: ChangeEvidence,
    *,
    signed_approval: SignedHumanApproval | None,
    approval_key: bytes,
    allowed_actor_ids: Collection[str],
    consume_nonce,
    now: int | None = None,
    max_lifetime_seconds: int = 900,
    max_future_skew_seconds: int = 30,
) -> GateResult:
    """Verify a signed approval bound to the exact artifact digest.

    The caller-supplied legacy boolean is never trusted. Nonce consumption
    must be atomic and durable; storage failures propagate (fail closed).
    """
    unapproved = replace(evidence, human_approval=False)
    result = evaluate_change(unapproved)
    if result.decision is GateDecision.DENY:
        return result

    needs_approval = (
        evidence.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        or evidence.production_release_requested
    )
    if not needs_approval:
        if signed_approval is not None:
            return GateResult(
                GateDecision.DENY,
                ("unexpected_human_approval_for_non_release_change",),
                result.verified_sha256,
            )
        return result

    if signed_approval is None:
        return result

    valid, reason = verify_human_approval(
        signed_approval,
        key=approval_key,
        expected_action_sha256=result.verified_sha256,
        allowed_actor_ids=allowed_actor_ids,
        consume_nonce=consume_nonce,
        now=now,
        max_lifetime_seconds=max_lifetime_seconds,
        max_future_skew_seconds=max_future_skew_seconds,
    )
    if not valid:
        return GateResult(
            GateDecision.DENY,
            (f"human_approval_{reason}",),
            result.verified_sha256,
        )

    return evaluate_change(replace(unapproved, human_approval=True))
