# AI Orchestration Foundation

## Current implementation
- The core chat API has a guarded OpenAI-compatible chat-completions adapter with primary and explicitly selected fallback.
- The core chat route now uses an explicit capability-aware model router over OpenAI-compatible, OpenAI, Groq, OpenRouter, Together, native Anthropic, native Gemini, and opt-in loopback-local adapters. Provider origins are fixed for named hosted providers; custom origins are restricted to the explicit OpenAI-compatible route.
- Live calls are disabled by default. No provider credentials or paid calls are used in CI.
- The organization AI chat route is restricted to owners/admins, requires explicit configuration, a positive rolling 24-hour request limit, and PostgreSQL quota serialization.
- AI audit events omit prompt and response content.

## Autonomy and approval controls
- A fail-closed gate evaluates artifact integrity, sandbox evidence, tests, security scans, regression evidence, rollback readiness, risk and approval.
- HMAC-SHA-256 attestation binds evidence to an artifact digest. SQLite provides atomic single-host nonce storage for local development; `PostgresNonceStore` uses a shared PostgreSQL primary key and `INSERT ... ON CONFLICT DO NOTHING RETURNING` for atomic multi-worker approval replay protection (migration `0034_postgres_approval_nonces`).
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
- Provider selection is explicit; no automatic retry/failover is enabled. The client-declared data class is a policy hint, not automatic DLP or trusted content classification.
- Provider routing supports text generation only. Native Anthropic and Gemini adapters are integrated. The agent loop uses strict JSON decisions and a local static read-only tool gateway rather than native provider function-calling. There is no automatic fallback or health-based failover.
- Signed approvals are integrated as library-level policy helpers, not issued through a trusted identity service or exposed through a release API.
- SQLite nonce storage remains single-host only; production callers must explicitly inject `PostgresNonceStore` backed by the shared PostgreSQL engine. The approval policy helper accepts either backend through the `consume_nonce` contract.
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


## Provenance-aware memory (initial implementation)

The feature branch adds user-private and organization-shared memory records:
- `POST /api/v1/organizations/{organization_id}/ai/memory` creates a record. User-private memory is scoped to the authenticated user; shared memory requires an organization owner/admin.
- `GET /api/v1/organizations/{organization_id}/ai/memory?q=...` returns only the caller's private records plus permitted organization records.
- `GET .../ai/memory/{memory_id}` inspects one authorized record.
- `DELETE .../ai/memory/{memory_id}` archives a record; it does not physically erase its row.
- Records store a SHA-256 content fingerprint, source type/reference, confidence percentage, sensitivity, verification status, optional expiry, and supersession link. User-submitted records begin as `unverified`; the API cannot mark them verified.
- Search uses bounded BM25-style lexical ranking over at most 500 recent eligible records, with matched terms and scores returned. It is not vector search and does not yet use PostgreSQL full-text indexes or embeddings.
- Private memory is visible only to its owner. Organization memory is visible to members only at normal sensitivity; admins may also see sensitive records, and only the organization owner may see restricted shared records.
- Superseding creates a new record and marks the prior record superseded. Archiving is soft deletion. Retention automation, hard deletion/export workflows, verification attestations, and vector retrieval remain future work.


## Versioned skill registry (initial implementation)

The feature branch adds organization-owned skill versions:
- `POST /api/v1/organizations/{organization_id}/ai/skills` registers a new immutable draft version. A duplicate organization/skill/version tuple returns HTTP 409.
- `GET /api/v1/organizations/{organization_id}/ai/skills` lists drafts and disabled versions to owners/admins, but only approved versions to ordinary members.
- `GET /api/v1/organizations/{organization_id}/ai/skills/{skill_id}/{version}` retrieves an exact visible version.
- `POST .../skills/{skill_id}/{version}/approve` requires the organization owner. Approval rechecks the SHA-256 content digest and records the approver.
- `POST .../skills/{skill_id}/{version}/disable` requires the organization owner and removes the version from the member catalog.
- Skill content is immutable through the API; publish a new semantic version for changes. The stored content digest is checked on every read.
- Each manifest records source type/reference, license identifier, risk level, data classes, and required capability labels. These labels are descriptive metadata only; they do not grant access to tools or data.
- Skill bodies are instructional text and are never executed as code. Imported source/license claims are recorded, not independently certified.

There is not yet an automatic skill selector, bulk MUSE-skill importer, cross-organization global catalog, or tool gateway that consumes capability labels.


## Static read-only tool gateway (initial implementation)

The feature branch exposes a fixed tool catalogue through:
- `GET /api/v1/organizations/{organization_id}/ai/tools`
- `POST /api/v1/organizations/{organization_id}/ai/tools/{tool_name}/execute`

The only registered tools are:
- `platform.health` — database connectivity check.
- `organization.profile` — basic profile for the authenticated organization.
- `ai.memory.search` — scope-filtered memory retrieval with provenance and matched terms.
- `ai.skills.list` — skill versions visible to the caller's organization role.
- `ai.tasks.get` — task status for its creator or an organization owner/admin.

Each tool has a strict Pydantic input schema. Unknown tools and extra arguments fail closed. Calls are audited without recording raw arguments or results. Tool definitions are static code; users and model output cannot register handlers. All five tools are read-only. Capability labels are descriptive metadata, not grants. No arbitrary shell, SQL, filesystem, URL-fetch, generic HTTP, payment, accounting-write, or production-deployment tool is registered.

The registry is not yet invoked by the task worker or an LLM tool-call loop. It is a separately callable, authenticated gateway foundation.


## Durable constrained agent execution

An agent run is submitted as a normal durable task:
`POST /api/v1/organizations/{organization_id}/ai/tasks`

Example `input_payload`:
```json
{
  "goal": "Inspect the organization profile and summarize approved project context",
  "model_route": "primary",
  "task_type": "analyze",
  "data_class": "internal",
  "max_steps": 3,
  "max_output_tokens": 256
}
```

Set `task_type` to `agent.run` on the outer task request. The API validates the inner schema, requires live AI to be enabled, PostgreSQL, a positive rolling 24-hour request limit, and a configured model route. Agent tasks are forced to one attempt: paid model calls are not automatically replayed after a worker crash or provider failure.

Run the worker in a separate process/container using the same database and provider environment:
```bash
# Process at most one queued task
python -m app.ai.runtime.worker --once

# Poll continuously (run under a process supervisor)
python -m app.ai.runtime.worker --poll-seconds 2 --lease-seconds 300
```

The agent uses strict JSON decisions (`tool` or `final`), a maximum of five model turns, and only the static read-only tool catalogue. Tool results and skill content are treated as untrusted data. Each model call consumes one unit of the organization's rolling 24-hour AI request quota. No provider cost/token reservation is implemented.

Current limits: this is a single-agent loop, not a multi-agent planner; there are no write tools, memory-write tools, native provider function calls, per-turn durable checkpoints, task cancellation, automatic retry/failover, or automatic resumption. If a worker dies, the single-attempt task eventually expires and fails rather than replaying paid calls. Successful task results include a bounded tool/action trace.


## Cooperative task cancellation

`POST /api/v1/organizations/{organization_id}/ai/tasks/{task_id}/cancel` lets the task creator cancel their own queued/running task, or an organization owner/admin cancel any task in that organization.
- Queued tasks transition immediately to `cancelled`.
- Running tasks receive a `cancel_requested_at` timestamp and a `cancel_requested` event. The worker acknowledges the request at its next heartbeat or terminal transition, abandons the active step, clears its lease, and records `cancelled`.
- Completion and failure transitions recheck the cancellation flag under a row lock, so a pending cancellation wins over a later success/failure write.
- If cancellation arrives during an external model request, that request cannot be forcibly interrupted; the worker observes cancellation when the request returns. No new model turn or tool call should start after that checkpoint.
- Expired running tasks with a pending cancellation are marked cancelled rather than reclaimed.

Cancellation is cooperative, not a hard process kill. It does not yet interrupt an in-flight HTTP request or persist resumable per-turn agent checkpoints.


## Durable task and constrained agent runtime

The organization AI task API supports idempotent queued work, lease-based worker claims, heartbeat, bounded attempts, lifecycle events, and task inspection. Run the standalone worker using `python -m app.ai.runtime.worker --once` or `--poll-seconds 2 --lease-seconds 300`; use an external process supervisor for continuous operation.

Agent execution is submitted as task type `agent.run`. It requires live AI to be explicitly enabled, PostgreSQL quota serialization, a positive rolling 24-hour request limit, and a compatible explicit model route. The model returns strict JSON decisions; only the static six-tool read-only gateway is available. Agent tasks have a maximum of five model turns and one task attempt to prevent automatic replay of paid provider calls after worker failure. Each model turn consumes one request-quota unit; there is no token/cost reservation.

Cooperative cancellation is available at `POST /api/v1/organizations/{organization_id}/ai/tasks/{task_id}/cancel`. Creators can cancel their own tasks; organization owners/admins can cancel any task in their organization. Queued tasks cancel immediately. Running tasks observe cancellation at a heartbeat or terminal transition. A provider HTTP request already in flight cannot be forcibly interrupted.

The runtime is single-agent, not a multi-agent planner. It has no write tools, memory-write tools, per-turn durable checkpoint/resumption, task preemption, automatic provider retry/failover, or external worker supervisor. Do not represent it as a fully autonomous multi-agent system.
