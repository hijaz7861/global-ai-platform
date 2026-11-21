"""Bridge signed human approvals into the fail-closed staged-release gate."""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from hashlib import sha256
from typing import Protocol

from app.ai.autonomy_gate import ChangeEvidence, GateDecision, GateResult, evaluate_change
from app.ai.human_approval import SignedHumanApproval, verify_human_approval


class NonceConsumer(Protocol):
    def consume_nonce(self, nonce: str) -> bool: ...


def evaluate_change_with_signed_approval(
    evidence: ChangeEvidence,
    signed_approval: SignedHumanApproval,
    *,
    approval_key: bytes,
    allowed_actor_ids: Collection[str],
    nonce_store: NonceConsumer,
    now: int | None = None,
) -> GateResult:
    """Require a valid one-time approval bound to the exact artifact before release.

    Pass SQLiteNonceStore for local single-host use or PostgresNonceStore for
    shared production workers. Storage errors propagate and therefore fail closed.
    This function only returns a policy decision; it never deploys.
    """
    actual_digest = sha256(evidence.artifact).hexdigest()
    if not evidence.production_release_requested:
        return GateResult(
            GateDecision.DENY,
            ("production_release_not_requested",),
            actual_digest,
        )

    preliminary = evaluate_change(replace(evidence, human_approval=False))
    if preliminary.decision is GateDecision.DENY:
        return preliminary

    valid, reason = verify_human_approval(
        signed_approval,
        key=approval_key,
        expected_action_sha256=actual_digest,
        allowed_actor_ids=allowed_actor_ids,
        consume_nonce=nonce_store.consume_nonce,
        now=now,
    )
    if not valid:
        return GateResult(
            GateDecision.DENY,
            (f"signed_human_approval_{reason}",),
            actual_digest,
        )

    return evaluate_change(replace(evidence, human_approval=True))
