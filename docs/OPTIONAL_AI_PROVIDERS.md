# Optional AI Provider Registry

## Scope

`app/ai/provider_registry.py` adds a configuration-only registry. It is disabled unless `AI_CONNECTORS_ENABLED=true` (or `1` / `yes`) is explicitly set.

Supported provider families:
- OpenAI-compatible APIs: OpenAI, Groq, OpenRouter, Together AI, and an explicitly opted-in local loopback endpoint.
- Native Anthropic Messages API through `AnthropicProvider`.
- Native Google Gemini generateContent API through `GeminiProvider`.

Provider-specific keys and model IDs must be injected by the deployment secret manager; never commit keys to source control. The registry performs no network request during initialization.

## API billing and subscription distinction

OpenAI API access uses a separate API key and may be billed separately from ChatGPT subscription access. This code does not log into or reuse a consumer ChatGPT subscription. Anthropic and Gemini use their native API protocols and their own credentials; they are not forced through an OpenAI-compatible schema.

## Security controls

- Remote API origins are fixed in code for the registered providers; arbitrary remote base URLs are not accepted by the registry.
- HTTPS is required for hosted providers; local plain HTTP is intended only for loopback endpoints.
- Requests have bounded timeouts, response sizes, no redirects, validated model identifiers for native adapters, and sanitized provider errors.
- Unit tests use mocked HTTP transports; they do not call paid live APIs.
- This is not a complete SSRF or egress-control system. Deployment network policy and secret management remain required.
- The provider registry does not grant an AI agent tool permissions or production admin authority. Those are separate policy-gated capabilities.
