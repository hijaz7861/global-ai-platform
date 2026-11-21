from hashlib import sha256

import pytest

from app.ai.human_approval import (
    HumanApproval,
    sign_human_approval,
    verify_human_approval,
)


KEY = b"test-approval-signing-key-32-bytes!"
ACTION = sha256(b"release artifact v1").hexdigest()


def make_approval(**overrides):
    values = {
        "action_sha256": ACTION,
        "actor_id": "maintainer@example.test",
        "approval_id": "approval-2026-001",
        "issued_at": 1_000,
        "expires_at": 1_600,
        "nonce": "unique_nonce_123456789",
    }
    values.update(overrides)
    return HumanApproval(**values)


def verify(signed, **overrides):
    values = {
        "key": KEY,
        "expected_action_sha256": ACTION,
        "allowed_actor_ids": {"maintainer@example.test"},
        "consume_nonce": lambda nonce: True,
        "now": 1_100,
    }
    values.update(overrides)
    return verify_human_approval(signed, **values)


def test_valid_approval_is_action_bound_and_time_limited():
    signed = sign_human_approval(make_approval(), KEY)
    assert verify(signed) == (True, "approval_verified")


def test_wrong_action_digest_is_rejected():
    signed = sign_human_approval(make_approval(), KEY)
    result = verify(signed, expected_action_sha256=sha256(b"different artifact").hexdigest())
    assert result == (False, "action_digest_mismatch")


def test_actor_must_be_allowlisted():
    signed = sign_human_approval(make_approval(), KEY)
    result = verify(signed, allowed_actor_ids={"another-maintainer"})
    assert result == (False, "actor_not_authorized")


def test_expired_approval_is_rejected():
    signed = sign_human_approval(make_approval(expires_at=1_050), KEY)
    assert verify(signed) == (False, "approval_expired")


def test_excessively_long_approval_window_is_rejected():
    signed = sign_human_approval(make_approval(expires_at=2_000), KEY)
    assert verify(signed) == (False, "approval_lifetime_too_long")


def test_invalid_signature_is_rejected():
    signed = sign_human_approval(make_approval(), KEY)
    forged = type(signed)(payload=signed.payload, signature_hex="0" * 64)
    assert verify(forged) == (False, "approval_signature_invalid")


def test_replayed_approval_is_rejected():
    signed = sign_human_approval(make_approval(), KEY)
    assert verify(signed, consume_nonce=lambda nonce: False) == (False, "approval_replayed")


def test_future_approval_is_rejected():
    signed = sign_human_approval(make_approval(issued_at=1_200, expires_at=1_800), KEY)
    assert verify(signed, now=1_100) == (False, "approval_from_future")


def test_signing_rejects_short_keys():
    with pytest.raises(ValueError, match="at least 32 bytes"):
        sign_human_approval(make_approval(), b"short")


def test_nonce_store_is_not_called_for_unauthorized_actor():
    signed = sign_human_approval(make_approval(), KEY)
    consumed = []
    result = verify(
        signed,
        allowed_actor_ids={"different-actor"},
        consume_nonce=lambda nonce: consumed.append(nonce) or True,
    )
    assert result == (False, "actor_not_authorized")
    assert consumed == []
