"""High-confidence secret-pattern detection for outbound agent prompts.

This is a narrow egress guard, not comprehensive DLP or a substitute for a
secret manager, provider-side controls, or data-classification policy.
"""
from __future__ import annotations

import re


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----", re.IGNORECASE)),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe_secret_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("openrouter_api_key", re.compile(r"\bsk-or-v1-[A-Fa-f0-9]{32,}\b")),
    ("openai_api_key", re.compile(r"\bsk-(?!or-v1-|ant-)(?:proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-(?:api|admin)\d{2}-[A-Za-z0-9_-]{20,}\b")),
    ("groq_api_key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("npm_token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b")),
    ("pypi_token", re.compile(r"\bpypi-[A-Za-z0-9_-]{30,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("bearer_token", re.compile(
        r"\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{20,}\b",
        re.IGNORECASE,
    )),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("database_url_credentials", re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s/:@]+:[^\s/@]+@",
        re.IGNORECASE,
    )),
    ("azure_storage_connection_string", re.compile(
        r"\bDefaultEndpointsProtocol=https?;AccountName=[^;]+;AccountKey=[A-Za-z0-9+/=]{20,}",
        re.IGNORECASE,
    )),
    ("credential_assignment", re.compile(
        r"\b[A-Z0-9_]*(?:API[_-]?KEY|ACCESS[_-]?TOKEN|REFRESH[_-]?TOKEN|"
        r"CLIENT[_-]?SECRET|AWS_SECRET_ACCESS_KEY|PASSWORD|PASSWD|"
        r"PRIVATE[_-]?KEY|TOKEN)\b\s*[:=]\s*[\\\"']?[^\\s\\\"'\\x60,;]{16,}",
        re.IGNORECASE,
    )),
)


def detect_sensitive_data_types(value: str) -> tuple[str, ...]:
    """Return matched category names only; never return matched secret values."""
    if not isinstance(value, str):
        raise TypeError("Outbound content must be text")
    return tuple(name for name, pattern in _PATTERNS if pattern.search(value))
