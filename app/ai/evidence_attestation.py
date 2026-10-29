"""Authenticate sandbox evidence with HMAC-SHA-256 and replay-aware verification.

The caller must provide a durable, atomic nonce-consumption function in production.
This module deliberately does not implement storage or execute sandbox jobs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import hmac
import json
import re
import time
from typing import Callable


@dataclass(frozen=True)
class SandboxAttestation:
    artifact_sha256: str
    run_id: str
    sandbox_id: str
    tests_passed: bool
    security_scan_passed: bool
    regression_suite_passed: bool
    issued_at: int
    nonce: str


@dataclass(frozen=True)
class SignedAttestation:
    payload: SandboxAttestation
    signature_hex: str


def _canonical_bytes(payload: SandboxAttestation) -> bytes:
    return json.dumps(
        asdict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def sign_attestation(payload: SandboxAttestation, key: bytes) -> SignedAttestation:
    """Sign evidence; call only inside the trusted sandbox controller."""
    if len(key) < 32:
        raise ValueError("HMAC key must contain at least 32 bytes")
    return SignedAttestation(
        payload=payload,
        signature_hex=hmac.new(key, _canonical_bytes(payload), sha256).hexdigest(),
    )


def verify_attestation(
    signed: SignedAttestation,
    *,
    key: bytes,
    expected_artifact_sha256: str,
    consume_nonce: Callable[[str], bool],
    now: int | None = None,
    max_age_seconds: int = 900,
    max_future_skew_seconds: int = 30,
) -> tuple[bool, str]:
    """Verify authenticity, binding, freshness and single-use nonce.

    consume_nonce must atomically persist a nonce as consumed and return False
    for any previously consumed nonce. Storage errors must raise and fail closed.
    """
    if len(key) < 32:
        return False, "hmac_key_too_short"
    payload = signed.payload
    if not re.fullmatch(r"[0-9a-f]{64}", payload.artifact_sha256):
        return False, "invalid_artifact_digest"
    if not hmac.compare_digest(payload.artifact_sha256, expected_artifact_sha256.lower()):
        return False, "artifact_digest_mismatch"
    if not payload.run_id.strip() or not payload.sandbox_id.strip():
        return False, "missing_run_or_sandbox_identity"
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", payload.nonce):
        return False, "invalid_nonce"
    if not re.fullmatch(r"[0-9a-f]{64}", signed.signature_hex):
        return False, "invalid_signature_format"

    expected_signature = hmac.new(key, _canonical_bytes(payload), sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, signed.signature_hex):
        return False, "signature_invalid"

    current_time = int(time.time()) if now is None else now
    if payload.issued_at > current_time + max_future_skew_seconds:
        return False, "attestation_from_future"
    if current_time - payload.issued_at > max_age_seconds:
        return False, "attestation_expired"
    if payload.issued_at < 0 or max_age_seconds < 1 or max_future_skew_seconds < 0:
        return False, "invalid_time_policy"

    if not payload.tests_passed or not payload.security_scan_passed or not payload.regression_suite_passed:
        return False, "required_check_failed"

    # A durable implementation must atomically reject re-use across workers.
    if not consume_nonce(payload.nonce):
        return False, "nonce_replayed"

    return True, "attestation_verified"
