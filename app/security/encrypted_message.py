"""Authenticated at-rest encryption for workforce message bodies."""
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr
from sqlalchemy.types import Text, TypeDecorator

from app.core.config import settings

CIPHERTEXT_PREFIX = "fernet:v1:"


def _secret_value(value: SecretStr | str | None) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value or ""


def _message_fernet() -> MultiFernet | None:
    current = _secret_value(settings.workforce_message_encryption_key)
    if not current:
        return None
    previous = _secret_value(settings.workforce_message_previous_encryption_keys)
    raw_keys = [current, *[item.strip() for item in previous.split(",") if item.strip()]]
    try:
        return MultiFernet([Fernet(item.encode("ascii")) for item in raw_keys])
    except (ValueError, UnicodeEncodeError):
        return None


def message_encryption_ready() -> bool:
    """Return whether a valid current key and every configured previous key are present."""
    return _message_fernet() is not None


class EncryptedMessageText(TypeDecorator):
    """Store Fernet ciphertext and expose plaintext only inside the application process."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        fernet = _message_fernet()
        if fernet is None:
            raise RuntimeError("WORKFORCE_MESSAGE_ENCRYPTION_KEY is not configured or invalid")
        return CIPHERTEXT_PREFIX + fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        if not value.startswith(CIPHERTEXT_PREFIX):
            raise RuntimeError(
                "Unencrypted workforce message row; run migration 0045 before serving it"
            )
        fernet = _message_fernet()
        if fernet is None:
            raise RuntimeError("WORKFORCE_MESSAGE_ENCRYPTION_KEY is not configured or invalid")
        token = value[len(CIPHERTEXT_PREFIX):].encode("ascii")
        try:
            return fernet.decrypt(token).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError):
            raise RuntimeError("Workforce message ciphertext cannot be authenticated") from None
