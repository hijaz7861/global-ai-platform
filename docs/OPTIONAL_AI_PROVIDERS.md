# Optional AI Provider Registry

## Scope

`app/ai/provider_registry.py` adds a configuration-only registry for the existing OpenAI-compatible adapter. It is disabled unless `AI_CONNECTORS_ENABLED=true` (or `1` / `yes`) is explicitly set.

Registered remote providers use fixed HTTPS API origins:
- OpenAI API: `https://api.openai.com/v1`
- Groq OpenAI-compatible API
- OpenRouter OpenAI-compatible API
- Together AI OpenAI-compatible API

The registry requires a provider-specific API key and model ID. Credentials should be injected by the deployment secret manager and must never be committed to the repository.

## Local models

A self-hosted OpenAI-compatible endpoint can be selected with `AI_PROVIDER=local`, but only after explicitly setting `AI_ALLOW_LOCAL_ENDPOINTS=true`. The existing adapter restricts plain HTTP to loopback addresses. Do not expose local model endpoints to untrusted networks.

## Important compatibility note

This registry currently supports only OpenAI-compatible chat-completions endpoints. It does not implement native Anthropic Claude or Google Gemini API protocols, nor does it connect using a ChatGPT consumer subscription. OpenAI API access requires a separate API key and may be billed separately from ChatGPT subscription access. Native Claude/Gemini support should be added as separate provider-specific adapters with their own schemas, limits, and tests.

## Security boundary

- No arbitrary remote URL can be configured through this registry.
- Connectors are disabled by default.
- The registry performs no network request during initialization.
- The existing provider adapter uses bounded response size, timeout, no redirects, and sanitized error messages.
- This is not a full SSRF defense for all application code; network egress controls and secret management remain deployment responsibilities.
