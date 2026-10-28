"""Opt-in registry for allowlisted AI providers; no arbitrary remote endpoint support."""
from __future__ import annotations

import os
from collections.abc import Mapping

from app.ai.native_providers import AnthropicProvider, GeminiProvider
from app.ai.providers import OpenAICompatibleProvider

_PROVIDER_CONFIG = {
    "openai": ("https://api.openai.com/v1", "AI_OPENAI_API_KEY", "AI_OPENAI_MODEL"),
    "groq": ("https://api.groq.com/openai/v1", "AI_GROQ_API_KEY", "AI_GROQ_MODEL"),
    "openrouter": ("https://openrouter.ai/api/v1", "AI_OPENROUTER_API_KEY", "AI_OPENROUTER_MODEL"),
    "together": ("https://api.together.xyz/v1", "AI_TOGETHER_API_KEY", "AI_TOGETHER_MODEL"),
}


def create_configured_provider(*, environ: Mapping[str, str] | None = None):
    """Return a configured provider only when explicitly enabled."""
    env = os.environ if environ is None else environ
    if env.get("AI_CONNECTORS_ENABLED", "false").strip().lower() not in {"1", "true", "yes"}:
        return None

    name = env.get("AI_PROVIDER", "openai").strip().lower()
    if name in {"anthropic", "gemini"}:
        key_name, model_name = (
            ("AI_ANTHROPIC_API_KEY", "AI_ANTHROPIC_MODEL")
            if name == "anthropic"
            else ("AI_GEMINI_API_KEY", "AI_GEMINI_MODEL")
        )
        api_key, model = env.get(key_name, "").strip(), env.get(model_name, "").strip()
        if not api_key or not model:
            raise ValueError("The selected AI provider requires its API key and model configuration")
        if name == "anthropic":
            return AnthropicProvider(api_key=api_key, model=model)
        return GeminiProvider(api_key=api_key, model=model)

    if name == "local":
        if env.get("AI_ALLOW_LOCAL_ENDPOINTS", "false").strip().lower() not in {"1", "true", "yes"}:
            raise ValueError("Local AI endpoints require AI_ALLOW_LOCAL_ENDPOINTS=true")
        base_url = env.get("AI_LOCAL_BASE_URL", "").strip()
        api_key, model = env.get("AI_LOCAL_API_KEY", "").strip(), env.get("AI_LOCAL_MODEL", "").strip()
    else:
        config = _PROVIDER_CONFIG.get(name)
        if config is None:
            raise ValueError("Unsupported AI provider; use a registered provider name")
        base_url, key_name, model_name = config
        api_key, model = env.get(key_name, "").strip(), env.get(model_name, "").strip()

    if not api_key or not model:
        raise ValueError("The selected AI provider requires its API key and model configuration")
    return OpenAICompatibleProvider(api_key=api_key, base_url=base_url, model=model,
                                    provider_name=name)
