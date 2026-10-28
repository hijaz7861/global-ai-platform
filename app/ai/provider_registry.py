"""Opt-in registry for explicitly supported OpenAI-compatible AI endpoints.

The registry intentionally does not accept arbitrary remote base URLs. Provider
protocols that are not OpenAI-compatible need their own audited adapter.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from app.ai.providers import OpenAICompatibleProvider


_PROVIDER_CONFIG = {
    "openai": ("https://api.openai.com/v1", "AI_OPENAI_API_KEY", "AI_OPENAI_MODEL"),
    "groq": ("https://api.groq.com/openai/v1", "AI_GROQ_API_KEY", "AI_GROQ_MODEL"),
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "AI_OPENROUTER_API_KEY",
        "AI_OPENROUTER_MODEL",
    ),
    "together": (
        "https://api.together.xyz/v1",
        "AI_TOGETHER_API_KEY",
        "AI_TOGETHER_MODEL",
    ),
}


def create_configured_provider(
    *,
    environ: Mapping[str, str] | None = None,
) -> OpenAICompatibleProvider | None:
    """Return a provider only when AI is explicitly enabled; never log secrets.

    Supported remote providers use fixed HTTPS origins. A self-hosted local
    OpenAI-compatible endpoint is available only when explicitly enabled and
    must resolve to loopback according to the adapter's URL validation.
    """
    env = os.environ if environ is None else environ
    if env.get("AI_CONNECTORS_ENABLED", "false").strip().lower() not in {"1", "true", "yes"}:
        return None

    provider_name = env.get("AI_PROVIDER", "openai").strip().lower()
    if provider_name == "local":
        if env.get("AI_ALLOW_LOCAL_ENDPOINTS", "false").strip().lower() not in {
            "1", "true", "yes"
        }:
            raise ValueError("Local AI endpoints require AI_ALLOW_LOCAL_ENDPOINTS=true")
        base_url = env.get("AI_LOCAL_BASE_URL", "").strip()
        api_key = env.get("AI_LOCAL_API_KEY", "").strip()
        model = env.get("AI_LOCAL_MODEL", "").strip()
    else:
        config = _PROVIDER_CONFIG.get(provider_name)
        if config is None:
            raise ValueError("Unsupported AI provider; use a registered provider name")
        base_url, key_name, model_name = config
        api_key = env.get(key_name, "").strip()
        model = env.get(model_name, "").strip()

    if not api_key or not model:
        raise ValueError("The selected AI provider requires its API key and model configuration")

    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider_name=provider_name,
    )
