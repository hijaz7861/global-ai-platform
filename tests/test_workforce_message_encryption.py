"""Focused tests for authenticated workforce message encryption and key rotation."""
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.dialects.sqlite import dialect

from app.core.config import settings
from app.security.encrypted_message import (
    CIPHERTEXT_PREFIX,
    EncryptedMessageText,
    message_encryption_ready,
)


def test_message_body_is_authenticated_ciphertext_at_rest(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "workforce_message_encryption_key", SecretStr(key))
    monkeypatch.setattr(settings, "workforce_message_previous_encryption_keys", SecretStr(""))

    column = EncryptedMessageText()
    plaintext = "Sensitive workforce project coordination"
    stored = column.process_bind_param(plaintext, dialect())

    assert stored.startswith(CIPHERTEXT_PREFIX)
    assert plaintext not in stored
    assert column.process_result_value(stored, dialect()) == plaintext


def test_message_encryption_fails_closed_without_a_valid_current_key(monkeypatch):
    monkeypatch.setattr(settings, "workforce_message_encryption_key", None)
    monkeypatch.setattr(settings, "workforce_message_previous_encryption_keys", SecretStr(""))

    assert message_encryption_ready() is False
    with pytest.raises(RuntimeError, match="not configured or invalid"):
        EncryptedMessageText().process_bind_param("must not be stored", dialect())


def test_message_encryption_reads_previous_key_but_writes_with_current_key(monkeypatch):
    old_key = Fernet.generate_key()
    new_key = Fernet.generate_key()
    old_ciphertext = (
        CIPHERTEXT_PREFIX + Fernet(old_key).encrypt(b"retained message").decode("ascii")
    )
    monkeypatch.setattr(
        settings, "workforce_message_encryption_key", SecretStr(new_key.decode("ascii"))
    )
    monkeypatch.setattr(
        settings,
        "workforce_message_previous_encryption_keys",
        SecretStr(old_key.decode("ascii")),
    )

    column = EncryptedMessageText()
    assert column.process_result_value(old_ciphertext, dialect()) == "retained message"

    newly_stored = column.process_bind_param("new message", dialect())
    token = newly_stored[len(CIPHERTEXT_PREFIX):].encode("ascii")
    assert Fernet(new_key).decrypt(token) == b"new message"


def test_tampered_message_ciphertext_is_rejected(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "workforce_message_encryption_key", SecretStr(key))
    monkeypatch.setattr(settings, "workforce_message_previous_encryption_keys", SecretStr(""))

    with pytest.raises(RuntimeError, match="cannot be authenticated"):
        EncryptedMessageText().process_result_value(CIPHERTEXT_PREFIX + "tampered", dialect())


def test_legacy_plaintext_is_rejected_until_migration_encrypts_it(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "workforce_message_encryption_key", SecretStr(key))
    monkeypatch.setattr(settings, "workforce_message_previous_encryption_keys", SecretStr(""))

    with pytest.raises(RuntimeError, match="run migration 0045"):
        EncryptedMessageText().process_result_value("legacy plaintext", dialect())
