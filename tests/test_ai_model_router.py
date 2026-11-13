"""Tests for capability-aware model selection and explicit provider routing."""
from types import SimpleNamespace

import pytest

from app.ai.model_router import (
    ModelRouteError,
    ModelRouter,
    ModelProfile,
    create_model_router,
)
from app.ai.native_providers import AnthropicProvider, GeminiProvider
from app.ai.providers import OpenAICompatibleProvider


class StubProvider:
    async def complete(self, prompt: str, max_output_tokens: int):
        return {
            "provider": "stub",
            "model": "stub-model",
            "content": prompt,
            "input_tokens": 1,
            "output_tokens": min(1, max_output_tokens),
        }


def _profile(route="primary", allowed=frozenset({"public", "internal"})):
    return ModelProfile(
        route=route,
        provider_name="stub",
        model_name="stub-model",
        capabilities=frozenset({"text.generate"}),
        allowed_data_classes=allowed,
        max_output_tokens=128,
        supports_tools=False,
        adapter=StubProvider(),
    )


def _settings(**overrides):
    values = {
        "ai_primary_provider": "openai-compatible",
        "ai_fallback_provider": None,
        "ai_allow_local_endpoints": False,
        "ai_api_key": "primary-key",
        "ai_base_url": "https://gateway.example/v1",
        "ai_model": "primary-model",
        "ai_fallback_api_key": None,
        "ai_fallback_base_url": None,
        "ai_fallback_model": None,
        "ai_timeout_seconds": 10,
        "ai_max_response_bytes": 1024 * 1024,
        "ai_max_output_tokens": 512,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_model_router_selects_only_explicit_compatible_route():
    router = ModelRouter({"primary": _profile()})
    selected = router.select(
        route="primary", task_type="analyze", data_class="internal",
        max_output_tokens=64,
    )
    assert selected.provider_name == "stub"
    assert selected.supports_tools is False

    with pytest.raises(ModelRouteError, match="not configured"):
        router.select(
            route="fallback", task_type="chat", data_class="internal",
            max_output_tokens=64,
        )


@pytest.mark.parametrize("data_class", ["confidential", "restricted"])
def test_model_router_denies_sensitive_data_classes(data_class):
    router = ModelRouter({"primary": _profile()})
    with pytest.raises(ModelRouteError, match="data classification"):
        router.select(
            route="primary", task_type="chat", data_class=data_class,
            max_output_tokens=64,
        )


def test_model_router_rejects_unsupported_capabilities_and_token_limits():
    router = ModelRouter({"primary": _profile()})
    with pytest.raises(ModelRouteError, match="capabilities"):
        router.select(
            route="primary", task_type="chat", data_class="internal",
            max_output_tokens=64, required_capabilities={"tools.call"},
        )
    with pytest.raises(ModelRouteError, match="token count"):
        router.select(
            route="primary", task_type="chat", data_class="internal",
            max_output_tokens=129,
        )


def test_router_builds_native_anthropic_and_gemini_profiles():
    anthropic = create_model_router(_settings(ai_primary_provider="anthropic"))
    gemini = create_model_router(_settings(ai_primary_provider="gemini"))

    assert isinstance(anthropic.select(
        route="primary", task_type="chat", data_class="internal",
        max_output_tokens=128,
    ).adapter, AnthropicProvider)
    assert isinstance(gemini.select(
        route="primary", task_type="chat", data_class="internal",
        max_output_tokens=128,
    ).adapter, GeminiProvider)


def test_router_uses_fixed_origins_for_registered_compatible_providers():
    router = create_model_router(_settings(ai_primary_provider="groq"))
    profile = router.select(
        route="primary", task_type="chat", data_class="internal",
        max_output_tokens=128,
    )
    assert isinstance(profile.adapter, OpenAICompatibleProvider)
    assert profile.adapter._endpoint == "https://api.groq.com/openai/v1/chat/completions"


def test_router_supports_explicit_fallback_without_automatic_failover():
    router = create_model_router(_settings(
        ai_fallback_provider="gemini",
        ai_fallback_api_key="fallback-key",
        ai_fallback_model="gemini-test",
    ))
    primary = router.select(
        route="primary", task_type="chat", data_class="internal",
        max_output_tokens=128,
    )
    fallback = router.select(
        route="fallback", task_type="chat", data_class="internal",
        max_output_tokens=128,
    )
    assert primary.provider_name == "openai-compatible"
    assert fallback.provider_name == "gemini"


def test_local_provider_requires_opt_in_and_loopback_endpoint():
    with pytest.raises(ValueError, match="explicit AI_ALLOW_LOCAL_ENDPOINTS"):
        create_model_router(_settings(
            ai_primary_provider="local",
            ai_base_url="http://127.0.0.1:11434/v1",
        ))
    with pytest.raises(ValueError, match="must resolve to loopback"):
        create_model_router(_settings(
            ai_primary_provider="local",
            ai_allow_local_endpoints=True,
            ai_base_url="https://remote.example/v1",
        ))
    router = create_model_router(_settings(
        ai_primary_provider="local",
        ai_allow_local_endpoints=True,
        ai_base_url="http://127.0.0.1:11434/v1",
    ))
    assert router.select(
        route="primary", task_type="chat", data_class="internal",
        max_output_tokens=128,
    ).provider_name == "local"


def test_unknown_provider_name_is_rejected():
    with pytest.raises(ValueError, match="Unsupported AI provider"):
        create_model_router(_settings(ai_primary_provider="https://untrusted.invalid"))
