"""Tests for the provider adapter at the external HTTP boundary."""
import asyncio

import httpx
import pytest

from app.ai.providers import OpenAICompatibleProvider, ProviderError


def test_openai_compatible_provider_sends_bounded_non_streaming_request():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "response text"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    provider = OpenAICompatibleProvider(
        api_key="test-secret",
        base_url="https://provider.example/v1",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(provider.complete("hello", 32))
    assert captured["url"] == "https://provider.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-secret"
    assert '"max_tokens":32' in captured["body"]
    assert '"stream":false' in captured["body"]
    assert result["content"] == "response text"
    assert result["input_tokens"] == 12
    assert result["output_tokens"] == 7


def test_provider_does_not_expose_error_response_body():
    provider = OpenAICompatibleProvider(
        api_key="test-secret",
        base_url="https://provider.example/v1",
        model="test-model",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="sensitive provider error body")
        ),
    )
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.complete("hello", 16))
    assert "sensitive provider error body" not in str(error.value)


def test_provider_rejects_insecure_non_loopback_urls():
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(
            api_key="test-secret",
            base_url="http://provider.example/v1",
            model="test-model",
        )



def test_provider_rejects_oversized_response_without_buffering_unbounded_body():
    provider = OpenAICompatibleProvider(
        api_key="test-secret",
        base_url="https://provider.example/v1",
        model="test-model",
        max_response_bytes=1024,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 2048)
        ),
    )
    with pytest.raises(ProviderError, match="size limit"):
        asyncio.run(provider.complete("hello", 16))


def test_provider_rejects_declared_oversized_response_before_reading_body():
    provider = OpenAICompatibleProvider(
        api_key="test-secret",
        base_url="https://provider.example/v1",
        model="test-model",
        max_response_bytes=1024,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-length": "4096"}, content=b""
            )
        ),
    )
    with pytest.raises(ProviderError, match="size limit"):
        asyncio.run(provider.complete("hello", 16))
