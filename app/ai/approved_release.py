"""Bridge signed human approvals into the fail-closed staged-release gate.

This is a policy helper, not an HTTP endpoint or deployment executor. The caller
must authenticate the request, supply a trusted allowlist/key, and keep nonce
storage durable. It never deploys or performs external side effects.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from hashlib import sha256

from app.ai.autonomy_gate import ChangeEvidence, GateDecision, GateResult, evaluate_change
from app.ai.human_approval import (
    SignedHumanApproval,
    verify_human_approval_with_sqlite_store,
)
from app.ai.nonce_store import SQLiteNonceStore


def evaluate_change_with_signed_approval(
    evidence: ChangeEvidence,
    signed_approval: SignedHumanApproval,
    *,
    approval_key: bytes,
    allowed_actor_ids: Collection[str],
    nonce_store: SQLiteNonceStore,
    now: int | None = None,
) -> GateResult:
    """Require a valid one-time approval bound to the exact artifact before release.

    All artifact/evidence/rollback checks run before consuming the approval nonce.
    Approval cannot override a failed check or turn a sandbox-only request into a
    release request. The function only returns a decision; it never deploys.
    """
    actual_digest = sha256(evidence.artifact).hexdigest()
    if not evidence.production_release_requested:
        return GateResult(
            GateDecision.DENY,
            ("production_release_not_requested",),
            actual_digest,
        )

    # Ignore the legacy boolean supplied on the evidence object: this path accepts
    # only a cryptographically verified, action-bound approval.
    preliminary = evaluate_change(replace(evidence, human_approval=False))
    if preliminary.decision is GateDecision.DENY:
        return preliminary

    valid, reason = verify_human_approval_with_sqlite_store(
        signed_approval,
        key=approval_key,
        expected_action_sha256=actual_digest,
        allowed_actor_ids=allowed_actor_ids,
        nonce_store=nonce_store,
        now=now,
    )
    if not valid:
        return GateResult(
            GateDecision.DENY,
            (f"signed_human_approval_{reason}",),
            actual_digest,
        )

    return evaluate_change(replace(evidence, human_approval=True))
