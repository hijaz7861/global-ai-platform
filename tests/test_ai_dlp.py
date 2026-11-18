"""High-confidence secret-pattern detection tests; no live credentials are used."""
import pytest

from app.ai.dlp import detect_sensitive_data_types


@pytest.mark.parametrize(
    ("sample", "category"),
    [
        ("-----BEGIN RSA PRIVATE KEY-----", "private_key"),
        ("ghp_" + "A" * 36, "github_token"),
        ("AKIA" + "A" * 16, "aws_access_key_id"),
        ("xoxb-" + "A" * 24, "slack_token"),
        ("sk_live_" + "A" * 24, "stripe_secret_key"),
        ("sk-or-v1-" + "a" * 40, "openrouter_api_key"),
        ("sk-proj-" + "A" * 40, "openai_api_key"),
        ("sk-ant-api03-" + "A" * 40, "anthropic_api_key"),
        ("sk-ant-admin01-" + "A" * 40, "anthropic_api_key"),
        ("gsk_" + "A" * 32, "groq_api_key"),
        ("hf_" + "A" * 36, "huggingface_token"),
        ("glpat-" + "A" * 32, "gitlab_token"),
        ("npm_" + "A" * 36, "npm_token"),
        ("pypi-" + "A" * 50, "pypi_token"),
        ("AIza" + "A" * 35, "google_api_key"),
        ("Authorization: Bearer " + "A" * 32, "bearer_token"),
        ("eyJ" + "A" * 12 + "." + "B" * 12 + "." + "C" * 12, "jwt"),
        ("postgresql://app:" + "A" * 24 + "@db.internal/app", "database_url_credentials"),
        (
            "DefaultEndpointsProtocol=https;AccountName=sample;AccountKey=" + "A" * 32,
            "azure_storage_connection_string",
        ),
        ("OPENAI_API_KEY=" + "a" * 40, "credential_assignment"),
        ("APP_ACCESS_TOKEN=" + "b" * 40, "credential_assignment"),
    ],
)
def test_detects_high_confidence_secret_patterns_without_returning_values(sample, category):
    detected = detect_sensitive_data_types(sample)
    assert category in detected
    assert sample not in repr(detected)


def test_provider_specific_patterns_do_not_overlap_between_openai_and_anthropic():
    assert detect_sensitive_data_types("sk-ant-api03-" + "A" * 40) == ("anthropic_api_key",)
    assert detect_sensitive_data_types("sk-or-v1-" + "a" * 40) == ("openrouter_api_key",)


def test_ordinary_security_discussion_and_redacted_examples_are_not_flagged():
    text = (
        "Explain API key rotation, password reset, token expiry, and private-key storage. "
        "Example configuration: OPENAI_API_KEY=<REDACTED>; "
        "sk-proj-<REDACTED>; sk-ant-api03-<REDACTED>."
    )
    assert detect_sensitive_data_types(text) == ()


def test_detector_requires_text_input():
    with pytest.raises(TypeError, match="must be text"):
        detect_sensitive_data_types({"token": "not text"})
