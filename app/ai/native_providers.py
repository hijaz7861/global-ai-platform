"""Native Anthropic and Google Gemini adapters with bounded, redacted HTTP handling."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.ai.providers import ProviderError


class _NativeProvider:
    def __init__(self, *, api_key: str, model: str, timeout_seconds: float = 20.0,
                 max_response_bytes: int = 1_048_576,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key.strip() or not model.strip():
            raise ValueError("Provider API key and model are required")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("Provider timeout must be between 1 and 120 seconds")
        if not 1024 <= max_response_bytes <= 10_485_760:
            raise ValueError("Provider response limit must be between 1024 and 10485760 bytes")
        self._api_key, self._model = api_key, model
        self._timeout, self._max_response_bytes = timeout_seconds, max_response_bytes
        self._transport = transport

    async def _post_json(self, url: str, *, headers: dict[str, str],
                         payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False,
                                     transport=self._transport) as client:
            try:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.is_error:
                        raise ProviderError(
                            f"AI provider returned HTTP {response.status_code}",
                            retryable=response.status_code == 429 or response.status_code >= 500,
                        )
                    length = response.headers.get("content-length")
                    if length is not None:
                        try:
                            if int(length) > self._max_response_bytes:
                                raise ProviderError("AI provider response exceeded the configured size limit")
                        except ValueError:
                            raise ProviderError("AI provider returned an invalid response length") from None
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > self._max_response_bytes:
                            raise ProviderError("AI provider response exceeded the configured size limit")
                        body.extend(chunk)
            except ProviderError:
                raise
            except httpx.TimeoutException as exc:
                raise ProviderError("AI provider request timed out", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise ProviderError("AI provider connection failed", retryable=True) from exc
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise ProviderError("AI provider returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("AI provider returned an unexpected response format")
        return parsed


class AnthropicProvider(_NativeProvider):
    """Native Anthropic Messages API adapter."""
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self._model):
            raise ValueError("Invalid Anthropic model identifier")

    async def complete(self, prompt: str, max_output_tokens: int) -> dict[str, Any]:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        if not 1 <= max_output_tokens <= 2048:
            raise ValueError("max_output_tokens must be between 1 and 2048")
        body = await self._post_json(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self._api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            payload={"model": self._model, "max_tokens": max_output_tokens,
                     "messages": [{"role": "user", "content": prompt}]},
        )
        try:
            content = "".join(block["text"] for block in body["content"]
                              if block.get("type") == "text")
            usage = body.get("usage", {})
            input_tokens, output_tokens = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ProviderError("AI provider returned an unexpected response format") from exc
        if (not isinstance(content, str) or not isinstance(input_tokens, int)
                or not isinstance(output_tokens, int) or input_tokens < 0 or output_tokens < 0):
            raise ProviderError("AI provider response contains invalid content or usage fields")
        return {"provider": "anthropic", "model": self._model, "content": content,
                "input_tokens": input_tokens, "output_tokens": output_tokens}


class GeminiProvider(_NativeProvider):
    """Native Google Gemini generateContent API adapter."""
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", self._model):
            raise ValueError("Invalid Gemini model identifier")

    async def complete(self, prompt: str, max_output_tokens: int) -> dict[str, Any]:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        if not 1 <= max_output_tokens <= 2048:
            raise ValueError("max_output_tokens must be between 1 and 2048")
        body = await self._post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
            headers={"x-goog-api-key": self._api_key, "content-type": "application/json"},
            payload={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                     "generationConfig": {"maxOutputTokens": max_output_tokens}},
        )
        try:
            parts = body["candidates"][0]["content"]["parts"]
            content = "".join(part["text"] for part in parts if isinstance(part.get("text"), str))
            usage = body.get("usageMetadata", {})
            input_tokens = usage.get("promptTokenCount", 0)
            output_tokens = usage.get("candidatesTokenCount", 0)
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderError("AI provider returned an unexpected response format") from exc
        if (not isinstance(content, str) or not isinstance(input_tokens, int)
                or not isinstance(output_tokens, int) or input_tokens < 0 or output_tokens < 0):
            raise ProviderError("AI provider response contains invalid content or usage fields")
        return {"provider": "gemini", "model": self._model, "content": content,
                "input_tokens": input_tokens, "output_tokens": output_tokens}
