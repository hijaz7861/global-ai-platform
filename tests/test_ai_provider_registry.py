import pytest

from app.ai.provider_registry import create_configured_provider


def test_connectors_are_disabled_by_default():
    assert create_configured_provider(environ={}) is None


def test_openai_provider_requires_key_and_model():
    with pytest.raises(ValueError, match="API key and model"):
        create_configured_provider(environ={"AI_CONNECTORS_ENABLED": "true"})


def test_openai_provider_uses_fixed_https_origin():
    provider = create_configured_provider(
        environ={
            "AI_CONNECTORS_ENABLED": "true",
            "AI_PROVIDER": "openai",
            "AI_OPENAI_API_KEY": "test-secret",
            "AI_OPENAI_MODEL": "test-model",
        }
    )
    assert provider is not None
    assert provider._endpoint == "https://api.openai.com/v1/chat/completions"
    assert provider._model == "test-model"


@pytest.mark.parametrize(
    ("provider_name", "key_name", "model_name", "expected_endpoint"),
    [
        ("groq", "AI_GROQ_API_KEY", "AI_GROQ_MODEL", "https://api.groq.com/openai/v1/chat/completions"),
        ("openrouter", "AI_OPENROUTER_API_KEY", "AI_OPENROUTER_MODEL", "https://openrouter.ai/api/v1/chat/completions"),
        ("together", "AI_TOGETHER_API_KEY", "AI_TOGETHER_MODEL", "https://api.together.xyz/v1/chat/completions"),
    ],
)
def test_registered_compatible_providers_use_fixed_origins(
    provider_name, key_name, model_name, expected_endpoint
):
    provider = create_configured_provider(
        environ={
            "AI_CONNECTORS_ENABLED": "true",
            "AI_PROVIDER": provider_name,
            key_name: "test-secret",
            model_name: "test-model",
        }
    )
    assert provider is not None
    assert provider._endpoint == expected_endpoint


def test_arbitrary_remote_provider_is_rejected():
    with pytest.raises(ValueError, match="Unsupported AI provider"):
        create_configured_provider(
            environ={
                "AI_CONNECTORS_ENABLED": "true",
                "AI_PROVIDER": "https://attacker.example",
            }
        )


def test_local_endpoint_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="AI_ALLOW_LOCAL_ENDPOINTS"):
        create_configured_provider(
            environ={
                "AI_CONNECTORS_ENABLED": "true",
                "AI_PROVIDER": "local",
                "AI_LOCAL_BASE_URL": "http://127.0.0.1:11434/v1",
                "AI_LOCAL_API_KEY": "local-test-key",
                "AI_LOCAL_MODEL": "local-model",
            }
        )


def test_local_endpoint_still_obeys_loopback_url_validation():
    with pytest.raises(ValueError, match="Provider URL must use HTTPS"):
        create_configured_provider(
            environ={
                "AI_CONNECTORS_ENABLED": "true",
                "AI_PROVIDER": "local",
                "AI_ALLOW_LOCAL_ENDPOINTS": "true",
                "AI_LOCAL_BASE_URL": "http://attacker.example/v1",
                "AI_LOCAL_API_KEY": "local-test-key",
                "AI_LOCAL_MODEL": "local-model",
            }
        )


def test_local_loopback_provider_can_be_configured():
    provider = create_configured_provider(
        environ={
            "AI_CONNECTORS_ENABLED": "true",
            "AI_PROVIDER": "local",
            "AI_ALLOW_LOCAL_ENDPOINTS": "true",
            "AI_LOCAL_BASE_URL": "http://127.0.0.1:11434/v1",
            "AI_LOCAL_API_KEY": "local-test-key",
            "AI_LOCAL_MODEL": "local-model",
        }
    )
    assert provider is not None
    assert provider._endpoint == "http://127.0.0.1:11434/v1/chat/completions"
