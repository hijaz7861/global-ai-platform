import asyncio
import json

import httpx
import pytest

from app.ai.native_providers import AnthropicProvider, GeminiProvider
from app.ai.provider_registry import create_configured_provider
from app.ai.providers import ProviderError


def test_anthropic_request_and_response():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), headers=request.headers, payload=json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hello"},
                                                      {"type": "text", "text": " world"}],
                                        "usage": {"input_tokens": 4, "output_tokens": 2}})

    provider = AnthropicProvider(api_key="test-key", model="claude-test",
                                 transport=httpx.MockTransport(handler))
    assert asyncio.run(provider.complete("say hello", 20)) == {
        "provider": "anthropic", "model": "claude-test", "content": "hello world",
        "input_tokens": 4, "output_tokens": 2}
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "test-key"
    assert seen["payload"]["max_tokens"] == 20


def test_gemini_request_and_response():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), headers=request.headers, payload=json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "answer"}]}}],
                                        "usageMetadata": {"promptTokenCount": 7,
                                                           "candidatesTokenCount": 3}})

    provider = GeminiProvider(api_key="test-key", model="gemini-test",
                              transport=httpx.MockTransport(handler))
    assert asyncio.run(provider.complete("question", 50)) == {
        "provider": "gemini", "model": "gemini-test", "content": "answer",
        "input_tokens": 7, "output_tokens": 3}
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    assert seen["headers"]["x-goog-api-key"] == "test-key"
    assert seen["payload"]["generationConfig"]["maxOutputTokens"] == 50


@pytest.mark.parametrize("factory", [
    lambda transport: AnthropicProvider(api_key="key", model="claude-test", transport=transport),
    lambda transport: GeminiProvider(api_key="key", model="gemini-test", transport=transport),
])
def test_errors_are_redacted(factory):
    provider = factory(httpx.MockTransport(
        lambda request: httpx.Response(429, text="sensitive provider response")))
    with pytest.raises(ProviderError, match="HTTP 429") as caught:
        asyncio.run(provider.complete("hello", 10))
    assert caught.value.retryable
    assert "sensitive provider response" not in str(caught.value)


def test_model_names_are_validated():
    with pytest.raises(ValueError, match="Invalid Anthropic"):
        AnthropicProvider(api_key="key", model="../messages")
    with pytest.raises(ValueError, match="Invalid Gemini"):
        GeminiProvider(api_key="key", model="model:generateContent?key=bad")


def test_native_providers_are_opt_in_and_require_secrets():
    assert create_configured_provider(environ={}) is None
    with pytest.raises(ValueError, match="API key and model"):
        create_configured_provider(environ={"AI_CONNECTORS_ENABLED": "true",
                                            "AI_PROVIDER": "gemini"})


def test_registry_selects_native_providers_without_network_calls():
    anthropic = create_configured_provider(environ={
        "AI_CONNECTORS_ENABLED": "true", "AI_PROVIDER": "anthropic",
        "AI_ANTHROPIC_API_KEY": "test-key", "AI_ANTHROPIC_MODEL": "claude-test"})
    gemini = create_configured_provider(environ={
        "AI_CONNECTORS_ENABLED": "true", "AI_PROVIDER": "gemini",
        "AI_GEMINI_API_KEY": "test-key", "AI_GEMINI_MODEL": "gemini-test"})
    assert isinstance(anthropic, AnthropicProvider)
    assert isinstance(gemini, GeminiProvider)


def test_empty_prompt_rejected():
    provider = GeminiProvider(api_key="key", model="gemini-test",
                              transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="Prompt must not be empty"):
        asyncio.run(provider.complete(" ", 10))


@pytest.mark.parametrize("provider", [AnthropicProvider, GeminiProvider])
def test_prompt_size_limit_is_enforced(provider):
    instance = provider(api_key="key", model="model-test",
                        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="Prompt exceeds"):
        asyncio.run(instance.complete("x" * 100_001, 10))
