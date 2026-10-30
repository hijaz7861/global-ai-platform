from hashlib import sha256

from app.ai.autonomy_gate import (
    ChangeEvidence,
    GateDecision,
    RiskLevel,
    evaluate_change_with_signed_approval,
)
from app.ai.human_approval import HumanApproval, sign_human_approval
from app.ai.nonce_store import SQLiteNonceStore


KEY = b"test-approval-signing-key-32-bytes!"
ACTOR = "maintainer@example.test"


def evidence_for(artifact=b"candidate release", **overrides):
    values = {
        "artifact": artifact,
        "claimed_sha256": sha256(artifact).hexdigest(),
        "sandbox_isolated": True,
        "tests_passed": True,
        "security_scan_passed": True,
        "regression_suite_passed": True,
        "rollback_ready": True,
        "risk": RiskLevel.HIGH,
        "human_approval": False,
        "production_release_requested": True,
    }
    values.update(overrides)
    return ChangeEvidence(**values)


def approval_for(artifact=b"candidate release", **overrides):
    values = {
        "action_sha256": sha256(artifact).hexdigest(),
        "actor_id": ACTOR,
        "approval_id": "approval-release-001",
        "issued_at": 1_000,
        "expires_at": 1_600,
        "nonce": "gate_nonce_1234567890",
    }
    values.update(overrides)
    return sign_human_approval(HumanApproval(**values), KEY)


def evaluate(tmp_path, evidence, approval, **overrides):
    store = SQLiteNonceStore(tmp_path / "approval-nonces.sqlite3")
    values = {
        "signed_approval": approval,
        "approval_key": KEY,
        "allowed_actor_ids": {ACTOR},
        "nonce_store": store,
        "now": 1_100,
    }
    values.update(overrides)
    return evaluate_change_with_signed_approval(evidence, **values)


def test_high_risk_release_without_signed_approval_requires_approval(tmp_path):
    result = evaluate(tmp_path, evidence_for(), None)
    assert result.decision is GateDecision.REQUIRE_HUMAN_APPROVAL


def test_valid_signed_approval_authorizes_only_staged_release(tmp_path):
    result = evaluate(tmp_path, evidence_for(), approval_for())
    assert result.decision is GateDecision.ELIGIBLE_FOR_STAGED_RELEASE
    assert result.verified_sha256 == sha256(b"candidate release").hexdigest()


def test_approval_for_different_artifact_is_denied(tmp_path):
    result = evaluate(
        tmp_path,
        evidence_for(),
        approval_for(artifact=b"different candidate"),
    )
    assert result.decision is GateDecision.DENY
    assert "human_approval_action_digest_mismatch" in result.reasons


def test_unapproved_actor_cannot_pass_release_gate(tmp_path):
    signed = approval_for(actor_id="untrusted@example.test")
    result = evaluate(tmp_path, evidence_for(), signed)
    assert result.decision is GateDecision.DENY
    assert "human_approval_actor_not_authorized" in result.reasons


def test_signed_approval_cannot_be_replayed_at_gate(tmp_path):
    signed = approval_for()
    first = evaluate(tmp_path, evidence_for(), signed)
    second = evaluate(tmp_path, evidence_for(), signed)
    assert first.decision is GateDecision.ELIGIBLE_FOR_STAGED_RELEASE
    assert second.decision is GateDecision.DENY
    assert "human_approval_approval_replayed" in second.reasons


def test_failed_security_evidence_denies_even_with_valid_approval(tmp_path):
    result = evaluate(
        tmp_path,
        evidence_for(security_scan_passed=False),
        approval_for(),
    )
    assert result.decision is GateDecision.DENY
    assert "security_scan_not_passed" in result.reasons


def test_low_risk_sandbox_does_not_require_approval(tmp_path):
    evidence = evidence_for(
        risk=RiskLevel.LOW,
        production_release_requested=False,
    )
    result = evaluate(tmp_path, evidence, None)
    assert result.decision is GateDecision.SANDBOX_ONLY
