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


## Durable task runtime (initial implementation)

The feature branch `feat/ai-agent-operating-layer-design` adds a first task-runtime slice without changing the existing chat route or enabling live provider calls.

- Submit tasks through `POST /api/v1/organizations/{organization_id}/ai/tasks` with a required `Idempotency-Key` header. The key is scoped to the submitting actor and organization; reusing it with a different task body returns HTTP 409.
- Task creation is restricted to organization owners/admins. The creator can inspect their task; owners/admins can inspect organization tasks.
- Inspect status through `GET /api/v1/organizations/{organization_id}/ai/tasks/{task_id}` and the minimal lifecycle timeline through the `/events` subroute.
- Queued tasks can be cancelled by their creator. Running tasks cannot yet be force-cancelled; worker cancellation is a future cooperative-cancellation feature.
- `app.ai.runtime.worker.run_worker_once` processes at most one task through an explicitly supplied handler registry. Unknown task types fail closed. No dynamic import, shell execution, model-generated code, or automatic provider call is performed.
- Worker claims use database-conditional state transitions and expiring leases. An expired lease can be reclaimed; a task whose attempts are exhausted becomes failed. Step records store input/output digests rather than duplicate raw payloads.
- Task lifecycle events deliberately omit prompts, provider output, credentials, and raw exception text.

This is a durable queue/runtime primitive, not yet a continuously supervised worker service, model planner, multi-agent coordinator, memory system, skill registry, or tool gateway. The task event table is not yet wired to the platform-wide audit export/retention pipeline. Production multi-worker operation requires PostgreSQL; SQLite is for local/unit development.

The implementation must pass the full Python matrix, migration consistency check, and PostgreSQL integration suite before it is considered CI-verified.
