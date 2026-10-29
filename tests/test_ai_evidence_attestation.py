from dataclasses import replace
from hashlib import sha256

from app.ai.evidence_attestation import (
    SandboxAttestation,
    sign_attestation,
    verify_attestation,
)


KEY = b"k" * 32
ARTIFACT = b"candidate patch"
DIGEST = sha256(ARTIFACT).hexdigest()


def make_signed(**overrides):
    values = {
        "artifact_sha256": DIGEST,
        "run_id": "run-123",
        "sandbox_id": "sandbox-abc",
        "tests_passed": True,
        "security_scan_passed": True,
        "regression_suite_passed": True,
        "issued_at": 1000,
        "nonce": "nonce_0123456789",
    }
    values.update(overrides)
    return sign_attestation(SandboxAttestation(**values), KEY)


def verifier(signed, *, expected_digest=DIGEST, consumed=None, now=1100, key=KEY):
    consumed = consumed if consumed is not None else set()

    def consume(nonce):
        if nonce in consumed:
            return False
        consumed.add(nonce)
        return True

    return verify_attestation(
        signed,
        key=key,
        expected_artifact_sha256=expected_digest,
        consume_nonce=consume,
        now=now,
    )


def test_valid_attestation_verifies_once():
    signed = make_signed()
    consumed = set()
    assert verifier(signed, consumed=consumed) == (True, "attestation_verified")
    assert verifier(signed, consumed=consumed) == (False, "nonce_replayed")


def test_modified_payload_invalidates_signature():
    signed = make_signed()
    tampered = replace(signed, payload=replace(signed.payload, tests_passed=False))
    assert verifier(tampered) == (False, "signature_invalid")


def test_artifact_digest_must_match_requested_artifact():
    assert verifier(make_signed(), expected_digest="0" * 64) == (
        False,
        "artifact_digest_mismatch",
    )


def test_expired_attestation_is_rejected():
    assert verifier(make_signed(issued_at=1), now=2000) == (
        False,
        "attestation_expired",
    )


def test_future_attestation_is_rejected():
    assert verifier(make_signed(issued_at=2000), now=1000) == (
        False,
        "attestation_from_future",
    )


def test_failed_security_check_is_rejected():
    signed = make_signed(security_scan_passed=False)
    assert verifier(signed) == (False, "required_check_failed")


def test_short_hmac_key_is_rejected_for_signing():
    import pytest

    with pytest.raises(ValueError, match="32 bytes"):
        sign_attestation(make_signed().payload, b"short")


def test_short_hmac_key_is_rejected_for_verification():
    assert verifier(make_signed(), key=b"short") == (False, "hmac_key_too_short")


def test_missing_sandbox_identity_is_rejected_before_nonce_consumption():
    signed = make_signed(sandbox_id=" ")
    assert verifier(signed) == (False, "missing_run_or_sandbox_identity")
