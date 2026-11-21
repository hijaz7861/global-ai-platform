from hashlib import sha256

from app.ai.autonomy_gate import ChangeEvidence, GateDecision, RiskLevel
from app.ai.human_approval import HumanApproval, sign_human_approval
from app.ai.release_gate import evaluate_change_with_approval


KEY = b"release-gate-approval-key-at-least-32"
ACTOR = "release-manager@example.test"


def evidence(**overrides):
    artifact = b"candidate release artifact"
    values = {
        "artifact": artifact,
        "claimed_sha256": sha256(artifact).hexdigest(),
        "sandbox_isolated": True,
        "tests_passed": True,
        "security_scan_passed": True,
        "regression_suite_passed": True,
        "rollback_ready": True,
        "risk": RiskLevel.LOW,
        "human_approval": True,  # deliberately untrusted legacy flag
        "production_release_requested": True,
    }
    values.update(overrides)
    return ChangeEvidence(**values)


def signed_for(e):
    payload = HumanApproval(
        action_sha256=sha256(e.artifact).hexdigest(),
        actor_id=ACTOR,
        approval_id="release-approval-001",
        issued_at=1_000,
        expires_at=1_600,
        nonce="release_nonce_123456789",
    )
    return sign_human_approval(payload, KEY)


def evaluate(e, approval=None, **overrides):
    values = {
        "signed_approval": approval,
        "approval_key": KEY,
        "allowed_actor_ids": {ACTOR},
        "consume_nonce": lambda nonce: True,
        "now": 1_100,
    }
    values.update(overrides)
    return evaluate_change_with_approval(e, **values)


def test_legacy_boolean_cannot_authorize_staged_release():
    result = evaluate(evidence())
    assert result.decision is GateDecision.REQUIRE_HUMAN_APPROVAL


def test_valid_signed_approval_allows_only_staged_release():
    e = evidence()
    result = evaluate(e, signed_for(e))
    assert result.decision is GateDecision.ELIGIBLE_FOR_STAGED_RELEASE


def test_approval_for_different_artifact_is_denied():
    e = evidence()
    other = evidence(artifact=b"different artifact",
                     claimed_sha256=sha256(b"different artifact").hexdigest())
    result = evaluate(e, signed_for(other))
    assert result.decision is GateDecision.DENY
    assert result.reasons == ("human_approval_action_digest_mismatch",)


def test_unauthorized_actor_is_denied():
    e = evidence()
    result = evaluate(e, signed_for(e), allowed_actor_ids={"another-manager"})
    assert result.decision is GateDecision.DENY
    assert result.reasons == ("human_approval_actor_not_authorized",)


def test_replayed_approval_is_denied():
    e = evidence()
    result = evaluate(e, signed_for(e), consume_nonce=lambda nonce: False)
    assert result.decision is GateDecision.DENY
    assert result.reasons == ("human_approval_approval_replayed",)


def test_technical_failure_denies_before_nonce_is_consumed():
    e = evidence(tests_passed=False)
    consumed = []
    result = evaluate(e, signed_for(e),
                      consume_nonce=lambda nonce: consumed.append(nonce) or True)
    assert result.decision is GateDecision.DENY
    assert consumed == []


def test_production_release_without_rollback_is_denied_even_with_approval():
    e = evidence(rollback_ready=False)
    result = evaluate(e, signed_for(e))
    assert result.decision is GateDecision.DENY
    assert "production_release_requires_rollback_plan" in result.reasons


def test_non_release_change_stays_sandbox_only():
    e = evidence(production_release_requested=False, risk=RiskLevel.LOW)
    result = evaluate(e)
    assert result.decision is GateDecision.SANDBOX_ONLY


def test_high_risk_change_requires_signed_approval():
    e = evidence(production_release_requested=False, risk=RiskLevel.HIGH)
    assert evaluate(e).decision is GateDecision.REQUIRE_HUMAN_APPROVAL
    assert evaluate(e, signed_for(e)).decision is GateDecision.SANDBOX_ONLY
