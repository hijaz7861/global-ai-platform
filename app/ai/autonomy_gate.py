"""Fail-closed policy gate for AI-proposed code changes.

This module evaluates evidence; it does not execute code or create a sandbox.
Execution must be delegated to an isolated runner with no production credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import hmac


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GateDecision(StrEnum):
    DENY = "deny"
    SANDBOX_ONLY = "sandbox_only"
    REQUIRE_HUMAN_APPROVAL = "require_human_approval"
    ELIGIBLE_FOR_STAGED_RELEASE = "eligible_for_staged_release"


@dataclass(frozen=True)
class ChangeEvidence:
    artifact: bytes
    claimed_sha256: str
    sandbox_isolated: bool
    tests_passed: bool
    security_scan_passed: bool
    regression_suite_passed: bool
    rollback_ready: bool
    risk: RiskLevel
    human_approval: bool = False
    production_release_requested: bool = False


@dataclass(frozen=True)
class GateResult:
    decision: GateDecision
    reasons: tuple[str, ...]
    verified_sha256: str


def evaluate_change(evidence: ChangeEvidence) -> GateResult:
    """Evaluate a proposed change using explicit evidence and fail-closed rules."""
    actual_digest = sha256(evidence.artifact).hexdigest()
    reasons: list[str] = []

    if not evidence.artifact:
        reasons.append("empty_artifact")
    if not evidence.claimed_sha256 or not hmac.compare_digest(
        actual_digest, evidence.claimed_sha256.lower()
    ):
        reasons.append("sha256_mismatch")
    if not evidence.sandbox_isolated:
        reasons.append("sandbox_not_isolated")
    if not evidence.tests_passed:
        reasons.append("tests_not_passed")
    if not evidence.security_scan_passed:
        reasons.append("security_scan_not_passed")
    if not evidence.regression_suite_passed:
        reasons.append("regression_suite_not_passed")

    if reasons:
        return GateResult(GateDecision.DENY, tuple(reasons), actual_digest)

    if evidence.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) and not evidence.human_approval:
        return GateResult(
            GateDecision.REQUIRE_HUMAN_APPROVAL,
            ("high_impact_change_requires_human_approval",),
            actual_digest,
        )

    if evidence.production_release_requested and not evidence.rollback_ready:
        return GateResult(
            GateDecision.DENY,
            ("production_release_requires_rollback_plan",),
            actual_digest,
        )

    if not evidence.production_release_requested:
        return GateResult(
            GateDecision.SANDBOX_ONLY,
            ("evidence_passed_but_production_release_not_requested",),
            actual_digest,
        )

    if not evidence.human_approval:
        return GateResult(
            GateDecision.REQUIRE_HUMAN_APPROVAL,
            ("production_release_requires_human_approval",),
            actual_digest,
        )

    return GateResult(
        GateDecision.ELIGIBLE_FOR_STAGED_RELEASE,
        ("checks_passed_staged_release_only",),
        actual_digest,
    )
