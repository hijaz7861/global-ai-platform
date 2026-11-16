from hashlib import sha256

from app.ai.autonomy_gate import ChangeEvidence, GateDecision, RiskLevel
from app.ai.evidence_attestation import SandboxAttestation, sign_attestation
from app.ai.governed_release import evaluate_governed_change
from app.ai.human_approval import HumanApproval, sign_human_approval


ATTESTATION_KEY = b"test-attestation-key-with-at-least-32-bytes"
APPROVAL_KEY = b"test-approval-key-with-at-least-32-bytes"
NOW = 1_800_000_000


def _evidence(artifact: bytes, *, risk=RiskLevel.HIGH, production=True):
    return ChangeEvidence(
        artifact=artifact,
        claimed_sha256=sha256(artifact).hexdigest(),
        sandbox_isolated=False,
        tests_passed=False,
        security_scan_passed=False,
        regression_suite_passed=False,
        rollback_ready=True,
        risk=risk,
        human_approval=True,
        production_release_requested=production,
        action="production_release",
    )


def _attestation(artifact: bytes, *, tests=True, security=True, regression=True):
    payload = SandboxAttestation(
        artifact_sha256=sha256(artifact).hexdigest(),
        run_id="run-0001",
        sandbox_id="sandbox-0001",
        tests_passed=tests,
        security_scan_passed=security,
        regression_suite_passed=regression,
        issued_at=NOW,
        nonce="attestation-nonce-0001",
    )
    return sign_attestation(payload, ATTESTATION_KEY)


def _approval(artifact: bytes, *, actor="owner-1"):
    payload = HumanApproval(
        action_sha256=sha256(artifact).hexdigest(),
        actor_id=actor,
        approval_id="approval-0001",
        issued_at=NOW,
        expires_at=NOW + 300,
        nonce="human-approval-nonce-01",
        action="production_release",
    )
    return sign_human_approval(payload, APPROVAL_KEY)


def _nonce_consumer():
    consumed = set()

    def consume(nonce):
        if nonce in consumed:
            return False
        consumed.add(nonce)
        return True

    return consumed, consume


def test_high_risk_change_requires_approval_without_burning_attestation_nonce():
    artifact = b"candidate patch"
    attestation = _attestation(artifact)
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    result = evaluate_governed_change(
        _evidence(artifact),
        signed_attestation=attestation,
        attestation_key=ATTESTATION_KEY,
        consume_attestation_nonce=consume_attestation,
        signed_approval=None,
        approval_key=APPROVAL_KEY,
        allowed_actor_ids={"owner-1"},
        consume_approval_nonce=consume_approval,
        now=NOW,
    )

    assert result.decision is GateDecision.REQUIRE_HUMAN_APPROVAL
    assert attestation_nonces == set()
    assert approval_nonces == set()


def test_valid_attestation_and_human_approval_allow_staged_release():
    artifact = b"candidate patch"
    attestation = _attestation(artifact)
    approval = _approval(artifact)
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    result = evaluate_governed_change(
        _evidence(artifact),
        signed_attestation=attestation,
        attestation_key=ATTESTATION_KEY,
        consume_attestation_nonce=consume_attestation,
        signed_approval=approval,
        approval_key=APPROVAL_KEY,
        allowed_actor_ids={"owner-1"},
        consume_approval_nonce=consume_approval,
        now=NOW,
    )

    assert result.decision is GateDecision.ELIGIBLE_FOR_STAGED_RELEASE
    assert attestation_nonces == {attestation.payload.nonce}
    assert approval_nonces == {approval.payload.nonce}


def test_invalid_human_approval_does_not_consume_either_nonce():
    artifact = b"candidate patch"
    attestation = _attestation(artifact)
    approval = _approval(artifact, actor="unauthorized-user")
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    result = evaluate_governed_change(
        _evidence(artifact),
        signed_attestation=attestation,
        attestation_key=ATTESTATION_KEY,
        consume_attestation_nonce=consume_attestation,
        signed_approval=approval,
        approval_key=APPROVAL_KEY,
        allowed_actor_ids={"owner-1"},
        consume_approval_nonce=consume_approval,
        now=NOW,
    )

    assert result.decision is GateDecision.DENY
    assert "human_approval_actor_not_authorized" in result.reasons
    assert attestation_nonces == set()
    assert approval_nonces == set()


def test_caller_supplied_sandbox_booleans_cannot_bypass_attestation():
    artifact = b"candidate patch"
    invalid_attestation = _attestation(artifact, security=False)
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    result = evaluate_governed_change(
        _evidence(artifact),
        signed_attestation=invalid_attestation,
        attestation_key=ATTESTATION_KEY,
        consume_attestation_nonce=consume_attestation,
        signed_approval=_approval(artifact),
        approval_key=APPROVAL_KEY,
        allowed_actor_ids={"owner-1"},
        consume_approval_nonce=consume_approval,
        now=NOW,
    )

    assert result.decision is GateDecision.DENY
    assert result.reasons == ("sandbox_attestation_required_check_failed",)
    assert attestation_nonces == set()
    assert approval_nonces == set()


def test_attestation_is_bound_to_exact_patch_digest():
    artifact = b"candidate patch"
    attestation = _attestation(artifact)
    altered = _evidence(b"different patch")
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    result = evaluate_governed_change(
        altered,
        signed_attestation=attestation,
        attestation_key=ATTESTATION_KEY,
        consume_attestation_nonce=consume_attestation,
        signed_approval=None,
        approval_key=APPROVAL_KEY,
        allowed_actor_ids={"owner-1"},
        consume_approval_nonce=consume_approval,
        now=NOW,
    )

    assert result.decision is GateDecision.DENY
    assert result.reasons == ("sandbox_attestation_artifact_digest_mismatch",)
    assert attestation_nonces == set()
    assert approval_nonces == set()


def test_low_risk_change_can_enter_sandbox_only_after_attestation():
    artifact = b"documentation update"
    attestation = _attestation(artifact)
    attestation_nonces, consume_attestation = _nonce_consumer()
    approval_nonces, consume_approval = _nonce_consumer()

    result = evaluate_governed_change(
        _evidence(artifact, risk=RiskLevel.LOW, production=False),
        signed_attestation=attestation,
        attestation_key=ATTESTATION_KEY,
        consume_attestation_nonce=consume_attestation,
        signed_approval=None,
        approval_key=APPROVAL_KEY,
        allowed_actor_ids={"owner-1"},
        consume_approval_nonce=consume_approval,
        now=NOW,
    )

    assert result.decision is GateDecision.SANDBOX_ONLY
    assert attestation_nonces == {attestation.payload.nonce}
    assert approval_nonces == set()
