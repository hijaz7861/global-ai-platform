"""Capability-aware, explicit model routing over allowlisted provider adapters.

The router never silently falls back, never accepts model-supplied endpoints, and currently
supports text generation only. Tool calling and cost estimates are intentionally unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.ai.native_providers import AnthropicProvider, GeminiProvider
from app.ai.providers import OpenAICompatibleProvider


_FIXED_ORIGINS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
}
_ALLOWED_TASKS = {"chat", "summarize", "analyze", "extract"}
_ALLOWED_DATA_CLASSES = {"public", "internal"}
_TEXT_CAPABILITY = "text.generate"


class ModelRouteError(ValueError):
    """No configured model satisfies the explicit route and policy."""


@dataclass(frozen=True)
class ModelProfile:
    route: str
    provider_name: str
    model_name: str
    capabilities: frozenset[str]
    allowed_data_classes: frozenset[str]
    max_output_tokens: int
    supports_tools: bool = False
    estimated_input_cost_per_million: int | None = None
    estimated_output_cost_per_million: int | None = None
    adapter: Any = field(default=None, repr=False, compare=False)


class ModelRouter:
    def __init__(self, profiles: Mapping[str, ModelProfile]) -> None:
        self._profiles = dict(profiles)

    def select(
        self,
        *,
        route: str,
        task_type: str,
        data_class: str,
        max_output_tokens: int,
        required_capabilities: set[str] | frozenset[str] | None = None,
    ) -> ModelProfile:
        if task_type not in _ALLOWED_TASKS:
            raise ModelRouteError("Unsupported AI task type")
        profile = self._profiles.get(route)
        if profile is None:
            raise ModelRouteError("The explicitly selected AI route is not configured")
        if data_class not in profile.allowed_data_classes:
            raise ModelRouteError("Selected provider is not approved for this data classification")
        if not 1 <= max_output_tokens <= profile.max_output_tokens:
            raise ModelRouteError("Requested output token count exceeds the selected model profile")
        required = set(required_capabilities or {_TEXT_CAPABILITY})
        if not required.issubset(profile.capabilities):
            raise ModelRouteError("Selected model does not support the required capabilities")
        return profile

    async def complete(
        self,
        *,
        route: str,
        task_type: str,
        data_class: str,
        prompt: str,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        profile = self.select(
            route=route,
            task_type=task_type,
            data_class=data_class,
            max_output_tokens=max_output_tokens,
        )
        return await profile.adapter.complete(prompt, max_output_tokens)


def _make_profile(
    *,
    route: str,
    provider_name: str,
    api_key: str | None,
    base_url: str | None,
    model_name: str | None,
    timeout_seconds: int,
    max_response_bytes: int,
    max_output_tokens: int,
    allow_local_endpoints: bool,
) -> ModelProfile:
    name = provider_name.strip().lower()
    key = (api_key or "").strip()
    model = (model_name or "").strip()
    if not key or not model:
        raise ValueError(f"AI {route} provider configuration is incomplete: API key and model are required")

    if name == "anthropic":
        adapter = AnthropicProvider(
            api_key=key, model=model, timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
    elif name == "gemini":
        adapter = GeminiProvider(
            api_key=key, model=model, timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
    elif name in _FIXED_ORIGINS:
        adapter = OpenAICompatibleProvider(
            api_key=key,
            base_url=_FIXED_ORIGINS[name],
            model=model,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            provider_name=name,
        )
    elif name == "local":
        if not allow_local_endpoints:
            raise ValueError("Local AI endpoints require explicit AI_ALLOW_LOCAL_ENDPOINTS=true")
        if not base_url:
            raise ValueError("Local AI provider requires an explicit loopback endpoint")
        adapter = OpenAICompatibleProvider(
            api_key=key, base_url=base_url, model=model,
            timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes,
            provider_name="local",
        )
    elif name == "openai-compatible":
        if not base_url:
            raise ValueError(f"AI {route} provider configuration is incomplete: base URL is required")
        adapter = OpenAICompatibleProvider(
            api_key=key, base_url=base_url, model=model,
            timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes,
            provider_name=name,
        )
    else:
        raise ValueError("Unsupported AI provider; use an allowlisted provider name")

    return ModelProfile(
        route=route,
        provider_name=name,
        model_name=model,
        capabilities=frozenset({_TEXT_CAPABILITY}),
        allowed_data_classes=frozenset(_ALLOWED_DATA_CLASSES),
        max_output_tokens=max_output_tokens,
        supports_tools=False,
        adapter=adapter,
    )


def create_model_router(config: Any) -> ModelRouter:
    """Build primary and optional fallback profiles from server-side configuration only."""
    profiles: dict[str, ModelProfile] = {}
    primary_name = getattr(config, "ai_primary_provider", "openai-compatible") or "openai-compatible"
    profiles["primary"] = _make_profile(
        route="primary",
        provider_name=primary_name,
        api_key=config.ai_api_key,
        base_url=config.ai_base_url,
        model_name=config.ai_model,
        timeout_seconds=config.ai_timeout_seconds,
        max_response_bytes=config.ai_max_response_bytes,
        max_output_tokens=config.ai_max_output_tokens,
        allow_local_endpoints=getattr(config, "ai_allow_local_endpoints", False),
    )

    fallback_values = (
        config.ai_fallback_api_key,
        config.ai_fallback_base_url,
        config.ai_fallback_model,
        getattr(config, "ai_fallback_provider", None),
    )
    if any(value and str(value).strip() for value in fallback_values):
        fallback_name = (
            getattr(config, "ai_fallback_provider", None)
            or primary_name
        )
        try:
            profiles["fallback"] = _make_profile(
                route="fallback",
                provider_name=fallback_name,
                api_key=config.ai_fallback_api_key,
                base_url=config.ai_fallback_base_url,
                model_name=config.ai_fallback_model,
                timeout_seconds=config.ai_timeout_seconds,
                max_response_bytes=config.ai_max_response_bytes,
                max_output_tokens=config.ai_max_output_tokens,
                allow_local_endpoints=getattr(config, "ai_allow_local_endpoints", False),
            )
        except ValueError as exc:
            raise ValueError("AI fallback provider configuration is incomplete or invalid") from exc

    return ModelRouter(profiles)
