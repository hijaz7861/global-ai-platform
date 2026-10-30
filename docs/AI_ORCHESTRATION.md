# AI Orchestration Foundation

## Current implementation
- The core chat API has a guarded OpenAI-compatible chat-completions adapter with primary and explicitly selected fallback.
- A separate optional provider registry and native Anthropic Messages and Google Gemini adapters exist with fixed provider origins, bounded HTTP behavior, and mocked tests.
- Live calls are disabled by default. No provider credentials or paid calls are used in CI.
- The organization AI chat route is restricted to owners/admins, requires explicit configuration, a positive rolling 24-hour request limit, and PostgreSQL quota serialization.
- AI audit events omit prompt and response content.

## Autonomy and approval controls
- A fail-closed gate evaluates artifact integrity, sandbox evidence, tests, security scans, regression evidence, rollback readiness, risk and approval.
- HMAC-SHA-256 attestation binds evidence to an artifact digest; a persistent SQLite nonce store provides atomic single-host replay protection.
- Signed human approvals bind a short-lived approval to an exact action/artifact digest and verify HMAC, actor allowlisting, expiry and one-time nonce consumption.
- `evaluate_change_with_signed_approval` connects signed-approval verification to the autonomy gate. Missing approval cannot authorize high/critical-risk changes or staged release; an invalid supplied approval denies. A valid approval only makes a change eligible for staged release after all other evidence and rollback checks pass.
- HMAC proves possession of a shared key, not a human identity. A trusted approval service must authenticate and authorize the actor before signing. Key management, identity/RBAC issuance workflow, durable approval audit integration and a public release API remain incomplete.
- The constrained Docker runner requires a digest-pinned image and rootless Docker, disables networking, uses a read-only root filesystem and mounts, drops capabilities, uses a non-root UID, and bounds resources/time/output. It has not been verified on a real rootless Docker host.

## Configure explicitly
Use environment variables documented in `.env.example`. Set `AI_LIVE_CALLS_ENABLED=true` only when external calls and charges are intentionally accepted. Configure a positive `AI_DAILY_REQUEST_LIMIT`, keep `AI_MAX_OUTPUT_TOKENS` low, and use a secret manager for deployed API keys.

## Planned Universal AI Connector Hub
The inbound MCP endpoint, runtime security gateway, encrypted secret-manager integration, capability-aware routing, tenant cost budgets, scoped tool permissions, OAuth/webhook lifecycle and staged release automation remain incomplete. No public MCP endpoint or production deployment is enabled.

## Current limitations
- Provider selection is explicit; no automatic retry/failover is enabled.
- Native Anthropic and Gemini adapters are not fully integrated into the core chat route or automatic router.
- Signed approvals are verified by the library gate but are not issued through a trusted identity service or exposed through a release API.
- SQLite nonce storage is single-host only.
- CI unit tests are not proof of real Docker isolation.
- No live provider credentials, paid API calls, payments, or deployment are used by tests.

Do not describe this as a fully autonomous multi-provider agent platform yet. It is a guarded AI adapter and API foundation with separate safety primitives.
