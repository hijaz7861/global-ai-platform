# AI Orchestration Foundation

## Current implementation
- The core chat API has a guarded OpenAI-compatible chat-completions HTTP adapter with primary and explicitly selected fallback endpoint/model.
- A separate optional provider registry allowlists OpenAI API, Groq, OpenRouter, Together AI, and explicitly enabled local loopback endpoints. Native Anthropic Messages and Google Gemini `generateContent` adapters are also present with provider-specific authentication and bounded HTTP behavior.
- The adapter uses HTTPS except for loopback testing, refuses redirects, applies a bounded timeout, caps output tokens and provider response bytes, makes no automatic retries, and never includes provider response bodies in public errors.
- `POST /api/v1/organizations/{organization_id}/ai/chat` is restricted to organization owners/admins.
- The request accepts `provider: primary|fallback`; `primary` is the default. Fallback selection requires all three `AI_FALLBACK_API_KEY`, `AI_FALLBACK_BASE_URL`, and `AI_FALLBACK_MODEL` values. Partial fallback configuration fails closed.
- The endpoint requires explicit configuration: live calls enabled, selected provider credentials/model, a positive rolling 24-hour request limit, and PostgreSQL for serialized quota reservations.
- Live calls are disabled by default. The daily request limit defaults to zero. No API keys are committed and no provider is contacted by CI tests.
- AI request start/success/failure audit events omit prompt and response content.

## Autonomy and approval primitives
- A fail-closed autonomy gate evaluates artifact integrity, sandbox evidence, tests, security scans, regression evidence, rollback readiness, risk, and approval flags.
- HMAC-SHA-256 attestation binds sandbox evidence to an artifact digest and checks freshness; a persistent SQLite nonce store provides atomic replay protection for a single host.
- `app/ai/human_approval.py` provides a separate action-digest-bound signed approval envelope. Verification checks HMAC, expected action digest, actor allowlist, short expiry, and one-time nonce consumption. The SQLite wrapper reuses the persistent single-host nonce store.
- HMAC proves that a shared-key holder signed the envelope; it does not prove a human identity. A trusted approval service must authenticate the actor and authorize the action before signing. Key storage, identity/RBAC integration, and binding the approval result into the runtime release endpoint remain unfinished.
- The constrained Docker runner requires a digest-pinned image and rootless Docker, disables networking, uses a read-only root filesystem and mounts, drops capabilities, uses a non-root UID, and bounds resources, time, and output. It has not been verified on a real rootless Docker host.

## Configure explicitly
Use environment variables documented in `.env.example`. Set `AI_LIVE_CALLS_ENABLED=true` only when the owner has intentionally enabled external provider calls and accepts any applicable charges. Configure `AI_DAILY_REQUEST_LIMIT` to a positive value and keep `AI_MAX_OUTPUT_TOKENS` low. `AI_MAX_RESPONSE_BYTES` bounds the provider response body (default 1 MiB). Use a secret manager for API keys in deployed environments.

## Planned Universal AI Connector Hub

The wider architecture is specified in [Universal AI Connector Hub](UNIVERSAL_AI_CONNECTOR_HUB.md) and the fail-closed admission design in [Security Gateway](SECURITY_GATEWAY.md). Outbound provider configuration, native Anthropic/Gemini adapters, signed autonomy evidence, SQLite nonce persistence, a constrained Docker sandbox runner, and a signed human-approval primitive now exist as separate components. The inbound MCP endpoint, runtime security gateway, encrypted secret-manager integration, capability-aware routing, tenant cost budgets, scoped tool permissions, OAuth/webhook lifecycle, and staged release automation remain incomplete. Live provider calls remain disabled unless explicitly configured; no public MCP endpoint or production deployment is enabled.

This design is not implemented by this document alone. The current runtime remains the guarded adapter described above.

## Current limitations
- Provider selection is explicit per request; there is no automatic retry or failover between providers.
- Native Anthropic and Gemini adapters are available as separate provider components, but they are not yet fully integrated into the core chat route or automatic router. Automatic multi-provider routing/fallback, pricing-aware monetary budgets, token usage reconciliation, user consent screens, tool calls, agent memory, and end-to-end human approval workflow remain incomplete.
- Signed human approvals are a verification primitive only; the module is not yet connected to a trusted identity service or release API.
- The Docker sandbox runner has not been exercised against a real rootless Docker daemon in this project; CI unit tests are not proof of runtime isolation.
- Request quotas count started requests over a rolling 24-hour window. Failed calls count toward the limit, intentionally conservative.
- Live calls require PostgreSQL row locking; SQLite deployments are deliberately refused for live AI calls.
- CI tests exercise the HTTP adapter using a local mock transport; no real provider credentials or paid API calls are used.

Do not describe this as a fully autonomous multi-provider agent platform yet. It is a guarded AI adapter and API foundation with separate safety primitives.
