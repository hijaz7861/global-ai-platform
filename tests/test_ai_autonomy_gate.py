from hashlib import sha256

from app.ai.autonomy_gate import (
    ChangeEvidence,
    GateDecision,
    RiskLevel,
    evaluate_change,
)


def evidence_for(artifact: bytes = b"safe patch", **overrides) -> ChangeEvidence:
    values = {
        "artifact": artifact,
        "claimed_sha256": sha256(artifact).hexdigest(),
        "sandbox_isolated": True,
        "tests_passed": True,
        "security_scan_passed": True,
        "regression_suite_passed": True,
        "rollback_ready": True,
        "risk": RiskLevel.LOW,
        "human_approval": False,
        "production_release_requested": False,
    }
    values.update(overrides)
    return ChangeEvidence(**values)


def test_valid_change_is_sandbox_only_by_default():
    result = evaluate_change(evidence_for())
    assert result.decision is GateDecision.SANDBOX_ONLY
    assert result.verified_sha256 == sha256(b"safe patch").hexdigest()


def test_digest_mismatch_fails_closed():
    result = evaluate_change(evidence_for(claimed_sha256="0" * 64))
    assert result.decision is GateDecision.DENY
    assert "sha256_mismatch" in result.reasons


def test_failed_security_scan_denies_change():
    result = evaluate_change(evidence_for(security_scan_passed=False))
    assert result.decision is GateDecision.DENY
    assert "security_scan_not_passed" in result.reasons


def test_non_isolated_sandbox_denies_change():
    result = evaluate_change(evidence_for(sandbox_isolated=False))
    assert result.decision is GateDecision.DENY
    assert "sandbox_not_isolated" in result.reasons


def test_high_risk_requires_human_approval():
    result = evaluate_change(evidence_for(risk=RiskLevel.HIGH))
    assert result.decision is GateDecision.REQUIRE_HUMAN_APPROVAL


def test_production_release_requires_approval_and_rollback():
    result = evaluate_change(
        evidence_for(production_release_requested=True, human_approval=True)
    )
    assert result.decision is GateDecision.ELIGIBLE_FOR_STAGED_RELEASE


def test_production_release_without_rollback_is_denied():
    result = evaluate_change(
        evidence_for(production_release_requested=True, rollback_ready=False, human_approval=True)
    )
    assert result.decision is GateDecision.DENY
    assert "production_release_requires_rollback_plan" in result.reasons


def test_empty_artifact_is_denied():
    result = evaluate_change(evidence_for(artifact=b"", claimed_sha256=sha256(b"").hexdigest()))
    assert result.decision is GateDecision.DENY
    assert "empty_artifact" in result.reasons


def test_missing_regression_evidence_denies_change():
    result = evaluate_change(evidence_for(regression_suite_passed=False))
    assert result.decision is GateDecision.DENY
    assert "regression_suite_not_passed" in result.reasons
