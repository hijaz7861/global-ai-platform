from hashlib import sha256

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.evidence_attestation import (
    SandboxAttestation,
    sign_attestation,
    verify_attestation_with_database,
)
from app.ai.nonce_store import ai_evidence_nonces


def test_database_backed_attestation_rejects_replay_across_calls():
    engine = create_engine("sqlite:///:memory:")
    ai_evidence_nonces.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifact_digest = sha256(b"candidate patch").hexdigest()
    signed = sign_attestation(
        SandboxAttestation(
            artifact_sha256=artifact_digest,
            run_id="run-123",
            sandbox_id="sandbox-abc",
            tests_passed=True,
            security_scan_passed=True,
            regression_suite_passed=True,
            issued_at=1000,
            nonce="nonce_0123456789",
        ),
        b"k" * 32,
    )

    kwargs = {
        "key": b"k" * 32,
        "expected_artifact_sha256": artifact_digest,
        "session_factory": factory,
        "now": 1100,
    }
    assert verify_attestation_with_database(signed, **kwargs) == (
        True,
        "attestation_verified",
    )
    assert verify_attestation_with_database(signed, **kwargs) == (
        False,
        "nonce_replayed",
    )
    engine.dispose()
