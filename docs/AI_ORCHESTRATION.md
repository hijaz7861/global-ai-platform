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
- Signed human approvals cryptographically bind both the exact artifact SHA-256 digest and a distinct action identifier. Verification checks signature, actor allowlisting, expiry, maximum lifetime, future clock skew and one-time nonce consumption.
- The gate evaluates artifact integrity, sandbox/test/security/regression evidence and rollback readiness before consuming the approval nonce. Invalid, expired, unauthorized, action-mismatched, digest-mismatched, tampered and replayed approvals fail closed.
- The legacy caller-controlled `human_approval` boolean is ignored by signed-approval integration paths. A valid approval only makes a change eligible for staged release after all other evidence and rollback checks pass.
- HMAC proves possession of a shared key, not a human identity. A trusted approval service must authenticate and authorize the actor before signing. Key management, identity/RBAC issuance workflow, durable approval audit integration and a public release API remain incomplete.
- The constrained Docker runner requires a digest-pinned image and rootless Docker, disables networking, uses a read-only root filesystem and mounts, drops capabilities, uses a non-root UID, and bounds resources/time/output. It has not been verified on a real rootless Docker host.

## Configure explicitly
Use environment variables documented in `.env.example`. Set `AI_LIVE_CALLS_ENABLED=true` only when external calls and charges are intentionally accepted. Configure a positive `AI_DAILY_REQUEST_LIMIT`, keep `AI_MAX_OUTPUT_TOKENS` low, and use a secret manager for deployed API keys.

## Planned Universal AI Connector Hub
The inbound MCP endpoint, runtime security gateway, encrypted secret-manager integration, capability-aware routing, tenant cost budgets, scoped tool permissions, OAuth/webhook lifecycle and staged release automation remain incomplete. No public MCP endpoint or production deployment is enabled.

## Current limitations
- Provider selection is explicit; no automatic retry/failover is enabled.
- Native Anthropic and Gemini adapters are not fully integrated into the core chat route or automatic router.
- Signed approvals are integrated as library-level policy helpers, not issued through a trusted identity service or exposed through a release API.
- SQLite nonce storage is single-host only.
- CI unit tests are not proof of real Docker isolation.
- No live provider credentials, paid API calls, payments, or deployment are used by tests.

Do not describe this as a fully autonomous multi-provider agent platform yet. It is a guarded AI adapter and API foundation with separate safety primitives.
