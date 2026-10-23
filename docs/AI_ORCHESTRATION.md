# AI Orchestration Foundation

## Current implementation
- An OpenAI-compatible chat-completions HTTP adapter supports configured API endpoints and model names.
- The adapter uses HTTPS except for loopback testing, refuses redirects, applies a bounded timeout, caps output tokens, makes no automatic retries, and never includes provider response bodies in public errors.
- `POST /api/v1/organizations/{organization_id}/ai/chat` is restricted to organization owners/admins.
- The endpoint requires explicit configuration: live calls enabled, API key, base URL, model, a positive rolling 24-hour request limit, and PostgreSQL for serialized quota reservations.
- Live calls are disabled by default. The daily request limit defaults to zero. No API keys are committed and no provider is contacted by CI tests.
- AI request start/success/failure audit events omit prompt and response content.

## Configure explicitly
Use environment variables documented in `.env.example`. Set `AI_LIVE_CALLS_ENABLED=true` only when the owner has intentionally enabled external provider calls and accepts any applicable charges. Configure `AI_DAILY_REQUEST_LIMIT` to a positive value and keep `AI_MAX_OUTPUT_TOKENS` low. Use a secret manager for API keys in deployed environments.

## Current limitations
- Only one OpenAI-compatible endpoint/model is selected by environment configuration per deployment.
- Provider-specific native APIs, automatic multi-provider routing/fallback, pricing-aware monetary budgets, token usage reconciliation, user consent screens, tool calls, agent memory, and human approval workflows are not implemented.
- Request quotas count started requests over a rolling 24-hour window. Failed calls count toward the limit, intentionally conservative.
- Live calls require PostgreSQL row locking; SQLite deployments are deliberately refused for live AI calls.
- CI tests exercise the HTTP adapter using a local mock transport; no real provider credentials or paid API calls are used.

Do not describe this as a multi-provider agent platform yet. It is a guarded adapter and API foundation.
