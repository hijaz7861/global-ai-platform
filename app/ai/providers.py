"""Explicitly configured OpenAI-compatible provider adapter.

This adapter performs no retries and never logs prompts, credentials, or provider response bodies.
"""
from typing import Any
from urllib.parse import urlsplit

import httpx


class ProviderError(RuntimeError):
    """Safe-to-report provider error that does not contain secrets or response bodies."""


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise ValueError("Provider URL must use HTTPS (HTTP is allowed only for loopback testing)")
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Provider URL must be an origin/path without credentials, query, or fragment")
        if not api_key.strip() or not model.strip():
            raise ValueError("Provider API key and model are required")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("Provider timeout must be between 1 and 120 seconds")
        self._api_key = api_key
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._timeout = timeout_seconds
        self._transport = transport

    async def complete(self, prompt: str, max_output_tokens: int) -> dict[str, Any]:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        if not 1 <= max_output_tokens <= 2048:
            raise ValueError("max_output_tokens must be between 1 and 2048")

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            try:
                response = await client.post(
                    self._endpoint,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                raise ProviderError("AI provider request timed out") from exc
            except httpx.HTTPError as exc:
                raise ProviderError("AI provider connection failed") from exc

        if response.is_error:
            raise ProviderError(f"AI provider returned HTTP {response.status_code}")
        try:
            body = response.json()
            choices = body["choices"]
            message = choices[0]["message"]
            content = message["content"]
            usage = body.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("AI provider returned an unexpected response format") from exc
        if not isinstance(content, str) or not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            raise ProviderError("AI provider response contains invalid content or usage fields")
        if input_tokens < 0 or output_tokens < 0:
            raise ProviderError("AI provider response contains invalid token usage")
        return {
            "provider": "openai-compatible",
            "model": self._model,
            "content": content,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
