import json

import httpx
import pytest

from app.ai.native_providers import AnthropicProvider, GeminiProvider
from app.ai.providers import ProviderError


@pytest.mark.asyncio
async def test_anthropic_native_request_and_response_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": " world"}],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        )

    provider = AnthropicProvider(
        api_key="unit-test-key",
        model="claude-test",
        transport=httpx.MockTransport(handler),
    )
    result = await provider.complete("say hello", 20)
    assert result == {
        "provider": "anthropic",
        "model": "claude-test",
        "content": "hello world",
        "input_tokens": 4,
        "output_tokens": 2,
    }
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "unit-test-key"
    assert seen["payload"]["max_tokens"] == 20


@pytest.mark.asyncio
async def test_gemini_native_request_and_response_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "Gemini answer"}]}}],
                "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
            },
        )

    provider = GeminiProvider(
        api_key="unit-test-key",
        model="gemini-test",
        transport=httpx.MockTransport(handler),
    )
    result = await provider.complete("question", 50)
    assert result == {
        "provider": "gemini",
        "model": "gemini-test",
        "content": "Gemini answer",
        "input_tokens": 7,
        "output_tokens": 3,
    }
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    assert seen["headers"]["x-goog-api-key"] == "unit-test-key"
    assert seen["payload"]["generationConfig"]["maxOutputTokens"] == 50


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda transport: AnthropicProvider(
            api_key="key", model="claude-test", transport=transport
        ),
        lambda transport: GeminiProvider(
            api_key="key", model="gemini-test", transport=transport
        ),
    ],
)
async def test_provider_errors_do_not_expose_response_body(provider_factory):
    def handler(request):
        return httpx.Response(429, text="secret-provider-error-body")

    provider = provider_factory(httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="HTTP 429") as caught:
        await provider.complete("hello", 10)
    assert caught.value.retryable is True
    assert "secret-provider-error-body" not in str(caught.value)


def test_model_identifiers_are_validated():
    with pytest.raises(ValueError, match="Invalid Anthropic"):
        AnthropicProvider(api_key="key", model="../messages")
    with pytest.raises(ValueError, match="Invalid Gemini"):
        GeminiProvider(api_key="key", model="x:generateContent?key=bad")


@pytest.mark.asyncio
async def test_empty_prompt_is_rejected_before_network():
    provider = GeminiProvider(
        api_key="key",
        model="gemini-test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    with pytest.raises(ValueError, match="Prompt must not be empty"):
        await provider.complete("  ", 10)
