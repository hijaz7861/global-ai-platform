from hashlib import sha256

from app.ai.approved_release import evaluate_change_with_signed_approval
from app.ai.autonomy_gate import ChangeEvidence, GateDecision, RiskLevel
from app.ai.human_approval import HumanApproval, sign_human_approval
from app.ai.nonce_store import SQLiteNonceStore


KEY = b"test-approval-signing-key-32-bytes!"
ARTIFACT = b"release-candidate-v1"
DIGEST = sha256(ARTIFACT).hexdigest()
ACTOR = "maintainer@example.test"


def evidence(**overrides):
    values = {
        "artifact": ARTIFACT,
        "claimed_sha256": DIGEST,
        "sandbox_isolated": True,
        "tests_passed": True,
        "security_scan_passed": True,
        "regression_suite_passed": True,
        "rollback_ready": True,
        "risk": RiskLevel.HIGH,
        "human_approval": True,  # Must not bypass signed approval verification.
        "production_release_requested": True,
    }
    values.update(overrides)
    return ChangeEvidence(**values)


def signed_approval(**overrides):
    values = {
        "action_sha256": DIGEST,
        "actor_id": ACTOR,
        "approval_id": "release-approval-001",
        "issued_at": 1_000,
        "expires_at": 1_600,
        "nonce": "release_nonce_123456789",
    }
    values.update(overrides)
    return sign_human_approval(HumanApproval(**values), KEY)


def evaluate(tmp_path, change, approval, **overrides):
    values = {
        "approval_key": KEY,
        "allowed_actor_ids": {ACTOR},
        "nonce_store": SQLiteNonceStore(tmp_path / "nonces.sqlite"),
        "now": 1_100,
    }
    values.update(overrides)
    return evaluate_change_with_signed_approval(change, approval, **values)


def test_valid_signed_approval_allows_staged_release_only(tmp_path):
    result = evaluate(tmp_path, evidence(), signed_approval())
    assert result.decision is GateDecision.ELIGIBLE_FOR_STAGED_RELEASE
    assert result.verified_sha256 == DIGEST


def test_legacy_boolean_cannot_replace_signed_approval(tmp_path):
    forged = signed_approval()
    forged = type(forged)(payload=forged.payload, signature_hex="0" * 64)
    result = evaluate(tmp_path, evidence(human_approval=True), forged)
    assert result.decision is GateDecision.DENY
    assert "signed_human_approval_approval_signature_invalid" in result.reasons


def test_approval_for_another_artifact_is_denied(tmp_path):
    approval = signed_approval(action_sha256=sha256(b"other artifact").hexdigest())
    result = evaluate(tmp_path, evidence(), approval)
    assert result.decision is GateDecision.DENY
    assert "signed_human_approval_action_digest_mismatch" in result.reasons


def test_failed_checks_do_not_consume_approval_nonce(tmp_path):
    approval = signed_approval()
    store = SQLiteNonceStore(tmp_path / "nonces.sqlite")
    result = evaluate(
        tmp_path,
        evidence(tests_passed=False),
        approval,
        nonce_store=store,
    )
    assert result.decision is GateDecision.DENY
    assert "tests_not_passed" in result.reasons
    assert store.consume_nonce(approval.payload.nonce) is True


def test_release_must_be_explicitly_requested(tmp_path):
    result = evaluate(tmp_path, evidence(production_release_requested=False), signed_approval())
    assert result.decision is GateDecision.DENY
    assert result.reasons == ("production_release_not_requested",)


def test_approval_cannot_bypass_missing_rollback_plan(tmp_path):
    result = evaluate(tmp_path, evidence(rollback_ready=False), signed_approval())
    assert result.decision is GateDecision.DENY
    assert "production_release_requires_rollback_plan" in result.reasons


def test_same_approval_cannot_be_reused(tmp_path):
    approval = signed_approval()
    store = SQLiteNonceStore(tmp_path / "nonces.sqlite")
    kwargs = {
        "approval_key": KEY,
        "allowed_actor_ids": {ACTOR},
        "nonce_store": store,
        "now": 1_100,
    }
    first = evaluate_change_with_signed_approval(evidence(), approval, **kwargs)
    second = evaluate_change_with_signed_approval(evidence(), approval, **kwargs)
    assert first.decision is GateDecision.ELIGIBLE_FOR_STAGED_RELEASE
    assert second.decision is GateDecision.DENY
    assert "signed_human_approval_approval_replayed" in second.reasons
