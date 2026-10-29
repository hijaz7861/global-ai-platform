"""Action-bound signed human approvals for staged AI changes.

HMAC authenticates possession of a shared approval key; it does not establish a
human identity by itself. Only a trusted approval service should sign after
authenticating the actor and checking authorization. Keep keys outside the repo.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import hmac
import json
import re
import time
from collections.abc import Callable, Collection


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACTOR = re.compile(r"^[A-Za-z0-9@._:-]{1,128}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass(frozen=True)
class HumanApproval:
    action_sha256: str
    actor_id: str
    approval_id: str
    issued_at: int
    expires_at: int
    nonce: str


@dataclass(frozen=True)
class SignedHumanApproval:
    payload: HumanApproval
    signature_hex: str


def _canonical_bytes(payload: HumanApproval) -> bytes:
    return json.dumps(
        asdict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def sign_human_approval(payload: HumanApproval, key: bytes) -> SignedHumanApproval:
    """Sign an approval only after a trusted service authenticates the human actor."""
    if len(key) < 32:
        raise ValueError("Approval HMAC key must contain at least 32 bytes")
    return SignedHumanApproval(
        payload=payload,
        signature_hex=hmac.new(key, _canonical_bytes(payload), sha256).hexdigest(),
    )


def verify_human_approval(
    signed: SignedHumanApproval,
    *,
    key: bytes,
    expected_action_sha256: str,
    allowed_actor_ids: Collection[str],
    consume_nonce: Callable[[str], bool],
    now: int | None = None,
    max_lifetime_seconds: int = 900,
    max_future_skew_seconds: int = 30,
) -> tuple[bool, str]:
    """Verify signature, action binding, actor allowlist, expiry and one-time use.

    Nonce consumption must be atomic and durable. Storage errors should propagate
    so callers fail closed. A valid signature is not a substitute for authenticating
    the actor at the approval-service boundary.
    """
    if len(key) < 32:
        return False, "approval_key_too_short"
    payload = signed.payload
    if not _DIGEST.fullmatch(payload.action_sha256):
        return False, "invalid_action_digest"
    if not _DIGEST.fullmatch(expected_action_sha256.lower()):
        return False, "invalid_expected_action_digest"
    if not hmac.compare_digest(payload.action_sha256, expected_action_sha256.lower()):
        return False, "action_digest_mismatch"
    if not _ACTOR.fullmatch(payload.actor_id):
        return False, "invalid_actor_id"
    if payload.actor_id not in allowed_actor_ids:
        return False, "actor_not_authorized"
    if not _ACTOR.fullmatch(payload.approval_id):
        return False, "invalid_approval_id"
    if not _NONCE.fullmatch(payload.nonce):
        return False, "invalid_nonce"
    if not isinstance(payload.issued_at, int) or not isinstance(payload.expires_at, int):
        return False, "invalid_approval_time"
    if payload.issued_at < 0 or payload.expires_at <= payload.issued_at:
        return False, "invalid_approval_window"
    if max_lifetime_seconds < 1 or max_future_skew_seconds < 0:
        return False, "invalid_time_policy"
    if payload.expires_at - payload.issued_at > max_lifetime_seconds:
        return False, "approval_lifetime_too_long"
    if not _DIGEST.fullmatch(signed.signature_hex):
        return False, "invalid_signature_format"

    expected_signature = hmac.new(key, _canonical_bytes(payload), sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, signed.signature_hex):
        return False, "approval_signature_invalid"

    current_time = int(time.time()) if now is None else now
    if payload.issued_at > current_time + max_future_skew_seconds:
        return False, "approval_from_future"
    if current_time >= payload.expires_at:
        return False, "approval_expired"
    if not consume_nonce(payload.nonce):
        return False, "approval_replayed"
    return True, "approval_verified"


def verify_human_approval_with_sqlite_store(
    signed: SignedHumanApproval,
    *,
    key: bytes,
    expected_action_sha256: str,
    allowed_actor_ids: Collection[str],
    nonce_store,
    now: int | None = None,
    max_lifetime_seconds: int = 900,
    max_future_skew_seconds: int = 30,
) -> tuple[bool, str]:
    """Verify using the existing persistent single-host SQLite nonce store."""
    return verify_human_approval(
        signed,
        key=key,
        expected_action_sha256=expected_action_sha256,
        allowed_actor_ids=allowed_actor_ids,
        consume_nonce=nonce_store.consume_nonce,
        now=now,
        max_lifetime_seconds=max_lifetime_seconds,
        max_future_skew_seconds=max_future_skew_seconds,
    )
