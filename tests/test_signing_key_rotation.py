"""Signing-key rotation tests."""
import jwt
import pytest

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token


def test_previous_signing_key_verifies_existing_token_until_removed(monkeypatch):
    old_key = "old-test-signing-key-0123456789abcdef"
    new_key = "new-test-signing-key-0123456789abcdef"
    monkeypatch.setattr(settings, "secret_key", old_key)
    monkeypatch.setattr(settings, "previous_secret_keys", "")
    old_token = create_access_token("rotation-user")

    monkeypatch.setattr(settings, "secret_key", new_key)
    monkeypatch.setattr(settings, "previous_secret_keys", old_key)
    assert decode_access_token(old_token)["sub"] == "rotation-user"

    new_token = create_access_token("rotation-user")
    assert decode_access_token(new_token)["sub"] == "rotation-user"

    monkeypatch.setattr(settings, "previous_secret_keys", "")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(old_token)


def test_weak_previous_signing_key_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "current-test-signing-key-0123456789")
    monkeypatch.setattr(settings, "previous_secret_keys", "too-short")
    with pytest.raises(RuntimeError, match="PREVIOUS_SECRET_KEYS"):
        decode_access_token("not-a-token")
