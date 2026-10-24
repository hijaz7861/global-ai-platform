"""TOTP generation, verification, and encryption for MFA secrets."""
import base64
import hashlib
import hmac
import os
import re
import struct
import time
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_AAD = b"global-ai-platform:mfa-secret:v1"
_CODE_PATTERN = re.compile(r"^\d{6}$")


def _encryption_key() -> bytes:
    if not settings.secret_key or len(settings.secret_key) < 32:
        raise RuntimeError("A SECRET_KEY of at least 32 characters is required for MFA")
    return hashlib.sha256((settings.secret_key + "|global-ai-platform|mfa-v1").encode("utf-8")).digest()


def encrypt_totp_secret(secret: str) -> str:
    nonce = os.urandom(12)
    encrypted = AESGCM(_encryption_key()).encrypt(nonce, secret.encode("ascii"), _AAD)
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii").rstrip("=")


def decrypt_totp_secret(value: str) -> str:
    try:
        packed = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        nonce, ciphertext = packed[:12], packed[12:]
        return AESGCM(_encryption_key()).decrypt(nonce, ciphertext, _AAD).decode("ascii")
    except Exception as exc:
        raise ValueError("Unable to decrypt MFA secret; verify SECRET_KEY continuity") from exc


def generate_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def generate_totp_code(secret: str, step: int | None = None) -> str:
    if step is None:
        step = int(time.time() // 30)
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def verify_totp(secret: str, code: str, *, last_step: int | None = None, now: float | None = None) -> int | None:
    if not _CODE_PATTERN.fullmatch(code):
        return None
    current_step = int((time.time() if now is None else now) // 30)
    for step in (current_step - 1, current_step, current_step + 1):
        if step < 0 or (last_step is not None and step <= last_step):
            continue
        if hmac.compare_digest(generate_totp_code(secret, step), code):
            return step
    return None


def make_otpauth_uri(secret: str, *, issuer: str, account_name: str) -> str:
    label = quote(f"{issuer}:{account_name}", safe="")
    return (
        f"otpauth://totp/{label}?secret={quote(secret)}&issuer={quote(issuer)}"
        "&algorithm=SHA1&digits=6&period=30"
    )
