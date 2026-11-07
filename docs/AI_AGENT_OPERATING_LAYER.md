# AI Agent Operating Layer — Architecture and Integration Plan

**Status:** Proposed architecture; implementation not yet started  
**Target:** Existing `hijaz7861/global-ai-platform` modular monolith  
**Baseline reviewed:** `master` at `f4a75813130b081688b7efe8c00a71f7d6d44201`  
**Reference system:** MUSE v3.10.0 at `aaba5300126b01f583d5ad5f15d6f548fc4f0a7d`

## 1. Executive decision

Build a first-party, tenant-aware AI Agent Operating Layer inside the existing Global AI Platform. Do not create a separate product or copy MUSE wholesale. Reuse the platform's FastAPI, SQLAlchemy, Alembic, identity, membership, audit, provider adapters, sandbox, and signed-approval foundations.

The layer will combine:
- durable task execution and resumable multi-agent workflows;
- provenance-aware, tenant-isolated long-term memory;
- versioned skills and a capability-controlled tool registry;
- model capability routing, quotas, and cost controls;
- deterministic policy decisions, human approvals, sandbox evidence, and auditable release gates;
- evaluation, tracing, and operational recovery.

Start as a modular monolith. Keep provider calls, tool execution, and background work behind narrow interfaces so workers can be split out later without redesigning domain contracts.

## 2. What MUSE actually provides

MUSE v3.10.0 is an AI coding governance and continuity framework, not an LLM, autonomous model, distributed agent runtime, or full database-backed memory service. It combines Markdown policies and roles, a 68-skill catalogue, local Python continuity scripts, file-based memory/source records, client adapters, optional MCP tools, and install/update utilities. It relies on an external AI client/model to reason and execute tools.

### Strengths worth learning from
- Clear constitution / role / skill separation and progressive context loading.
- Explicit continuity checkpoints, source evidence, provenance, and recovery workflows.
- Guarded file writes, stale-writer detection, atomic replacement, and verification.
- Coordinator synthesis before delegating work; independent verification after implementation.
- Evidence-first completion claims and explicit unknown states.
- Multi-client skill distribution and skill discovery/update workflows.
- Fail-closed thinking around high-risk actions and human review.

### Material limitations to avoid inheriting
- Core memory is primarily Markdown/files, not a transactional multi-tenant memory service.
- Core search uses zero-dependency TF-IDF; it is not vector retrieval or a semantic knowledge graph.
- The optional Bash MCP server has a separate write path and does not inherit all newer guarded checkpoint guarantees.
- Client integrations are not equivalent: adapters and prompt files do not make every client a fully native, durable orchestration runtime.
- File locks and SHA-256 manifests protect cooperative local workflows; they are not distributed consensus, immutable storage, or protection against a privileged local attacker.
- Some client hooks observe only events they receive; they cannot promise lossless conversation capture.
- Skills are instructions, not executable authorization. A skill must never grant itself tools or privileges.

### Installer finding
Static review of `scripts/install.sh` found post-increment expressions such as `((skill_count++))` under `set -euo pipefail`. In Bash, a post-increment whose expression evaluates to zero can return status 1, potentially terminating some install paths on the first item. This is a static finding, not a runtime reproduction. The installer also has tool-specific differences in which skill tiers are copied; cross-client parity should not be assumed.

## 3. Existing platform baseline

The current `master` branch is a public GitHub repository, with write access available to the connected account. It is a FastAPI modular monolith with SQLAlchemy/Alembic, SQLite for local tests, PostgreSQL for production/concurrency requirements, and Docker packaging.

Existing capabilities include:
- identity, Argon2 password hashing, rotating refresh sessions, token revocation, MFA, email verification, lockout, and rate limits;
- organizations, memberships, role checks, tenant-scoped catalog, orders, suppliers, procurement, accounting, and audit events;
- OpenAI-compatible provider adapter, explicit primary/fallback selection, a provider registry, and native Anthropic/Gemini adapters;
- AI usage limits and owner/admin checks on the current chat endpoint;
- fail-closed change eligibility policy, HMAC evidence attestation, action-bound signed human approvals, a persistent single-host SQLite nonce store, and a constrained rootless-Docker sandbox runner.

The AI foundation is deliberately incomplete:
- native provider adapters are not all connected to the primary chat route or an automatic capability router;
- no durable multi-agent task queue or resumable workflow engine exists;
- no tenant-scoped persistent memory service, memory provenance model, or hybrid retrieval exists;
- no governed skill registry or typed tool gateway exists;
- the nonce store is single-host SQLite, not a shared PostgreSQL implementation;
- the sandbox has not been validated on a real rootless Docker worker;
- signed approval helpers are not yet a complete trusted identity/approval service or public release API.

The current AI test files contain 89 test declarations across provider adapters, registry, route behavior, sandbox refusal/validation, attestation, nonce replay, and signed approvals. The latest `master` commit is signed and its CI workflow run #608 completed successfully on 2026-10-30. This is repository evidence, not a fresh local test run.

## 4. Architecture principles

1. **Tenant isolation by construction:** every durable memory, task, run, skill grant, tool grant, and usage record is scoped to a tenant/organization where applicable. Resolve actor and tenant from authenticated server-side membership, never from model output.
2. **Policy outside the model:** model output is untrusted. Only deterministic server-side policy can authorize tools, data access, or state transitions.
3. **Persist before work:** task acceptance, idempotency, state transitions, and audit events are committed before external execution begins.
4. **At-least-once execution, exactly-once effects:** workers may retry; business effects must be idempotent and guarded by durable idempotency keys and transactional constraints.
5. **Evidence-bound completion:** a task is complete only when acceptance criteria and required verification evidence are attached to its run.
6. **Least privilege:** read-only tools first; each agent receives only the capabilities required for its task.
7. **No silent routing:** provider/model selection, data sensitivity, cost ceiling, fallback, and external data transfer policy are explicit and auditable.
8. **Human approval for consequential actions:** payments, refunds, journal posting, external communications, permissions, destructive operations, sensitive exports, and production deployment require action-bound approval.
9. **Modular monolith first:** use internal interfaces and database-backed workers before introducing separate services or paid infrastructure.
10. **Honest status:** distinguish implemented, tested, CI-verified, staging-verified, and production-verified states.

## 5. Logical components

### 5.1 Agent runtime
A deterministic state machine accepts a task, creates a run, selects an agent profile, plans bounded steps, dispatches allowed tools, records observations, and produces a result with evidence. Agents are configuration and policy profiles, not independent security principals.

Suggested run states:
`queued → claimed → running → waiting_approval → succeeded | failed | cancelled`

Workers claim work using a lease and heartbeat. Expired leases are reclaimable. Each step has an idempotency key, bounded attempt count, timeout, input/output size limits, and a structured result. Cancellation and approval waits are explicit states, not prompt conventions.

### 5.2 Durable orchestration
Use PostgreSQL row locking (including `SKIP LOCKED` where supported) for concurrent worker claims. Use a transactional outbox for events that must be published after a database commit. SQLite remains useful for local development but is not the production multi-worker coordination backend.

Do not add Celery/Redis/Kafka as a prerequisite in phase one. Keep a queue interface so a durable external broker can be added when measured throughput requires it.

### 5.3 Memory service
Use layered memory with explicit semantics:
- **Working memory:** bounded current-run context; expires with the run.
- **Session memory:** concise session summaries and unresolved decisions.
- **Project memory:** durable project facts, architecture decisions, constraints, and verified progress.
- **Organization memory:** tenant-shared knowledge, subject to membership and data classification.
- **User preference memory:** user-controlled preferences, isolated from organization-shared facts.

Every memory record should include tenant/owner scope, type, content, source reference, content digest, created/updated timestamps, confidence, sensitivity, retention/expiry, verification state, and supersession relation. Support correction, deletion/retention policy, and provenance inspection. Never silently convert model inference into a verified fact.

Retrieval pipeline:
1. enforce actor/tenant/sensitivity filters before search;
2. apply exact identifiers and metadata filters;
3. retrieve using BM25-style lexical ranking (the first implementation uses a bounded in-process candidate set; PostgreSQL full-text indexing is a later scale optimization);
4. optionally add embeddings through a pluggable vector index (pgvector only when provisioned);
5. rerank a bounded candidate set;
6. return source citations, freshness, confidence, and why each result was selected.

Vector similarity is a recall aid, not a truth or authorization mechanism. If embeddings are unavailable, lexical and metadata retrieval must still work. The first implementation uses BM25-style ranking over at most 500 recent eligible records, preserves source hashes and provenance, and deliberately marks user-supplied memories unverified.

### 5.4 Skill registry
Represent a skill as versioned, signed-or-hash-pinned instructional content plus a manifest:
- stable skill ID, version, title, description, domain, compatibility;
- source/license, content digest, author/maintainer, review status;
- declared required capabilities, data classes, and risk level;
- test/evaluation fixtures and deprecation/supersession metadata.

Load skill instructions as untrusted reference data. A skill may describe a workflow but cannot issue grants, access secrets, change policy, or directly execute arbitrary code. Skill activation is decided by the runtime using task classification plus explicit allowlists; it must be possible to disable a skill centrally.

### 5.5 Tool gateway
Create a typed registry of narrow tools. Each tool declares schema, read/write classification, capability, tenant scope, side-effect class, timeout, output limit, idempotency behavior, and approval requirement.

Initial release should expose read-only tools only: platform health, current organization metadata, public catalog search, own order summaries, permitted accounting reports, and connector capability listing. Add draft-only write tools after policy and audit tests pass. Re-check authorization at execution time; do not rely on the model's earlier plan.

Never expose arbitrary shell, raw SQL, unrestricted filesystem access, arbitrary URL fetch, or a generic HTTP proxy as an agent tool.

### 5.6 Model router
Unify existing adapters behind a provider protocol with capability metadata:
- supported modalities and tool calling;
- context/output limits;
- structured-output support;
- data residency/sensitivity constraints;
- configured cost estimates, rate limits, timeout, and health;
- supported API dialect and model version.

Route by task capability, policy, user/tenant settings, and budget. Provider fallback must be explicit and must not silently move sensitive content to another vendor. Add per-tenant and per-user rolling budgets, token/usage accounting, concurrency limits, circuit breakers, and provider health. Unknown price or usage must be reported as unknown, not fabricated.

### 5.7 Governance and approvals
Build on the existing autonomy gate, attestation, signed approval, and audit foundations rather than replacing them. Extend approvals to bind:
- actor and tenant;
- exact tool/action and canonical arguments digest;
- target resource/environment and amount where relevant;
- policy version, expiry, nonce, and idempotency key.

The approval issuer must authenticate the human and check authorization before signing. HMAC alone does not prove human identity. Move production nonce consumption to a shared transactional PostgreSQL table with a uniqueness constraint and atomic insert. Preserve the current SQLite store for single-host local development only.

### 5.8 Observability and evaluation
Every run and step should carry correlation IDs and record agent/skill/model versions, policy decisions, tool calls, latency, token/estimated cost, retries, approvals, evidence references, and outcome. Redact prompts, credentials, and sensitive payloads by default.

Add deterministic evaluations for:
- task completion against acceptance criteria;
- tool selection and least-privilege compliance;
- memory recall precision, provenance, and tenant isolation;
- prompt-injection resistance at the tool boundary;
- approval binding, expiry, replay, and concurrent replay;
- retry/recovery after worker crash;
- provider timeout, quota, circuit-breaker, and budget behavior.

## 6. Proposed package boundaries

Keep the existing `app/ai` namespace and introduce these modules incrementally:

```text
app/ai/
  contracts.py              # provider, agent, task, tool and policy contracts
  runtime/
    service.py               # task/run lifecycle and orchestration
    worker.py                # lease-based worker loop
    planner.py                # bounded step planning
    recovery.py               # expired lease and interrupted run recovery
  memory/
    service.py                # memory lifecycle and tenant-scoped access
    retrieval.py              # lexical + optional vector + reranking
    provenance.py             # source, digest, confidence, supersession
  skills/
    registry.py               # manifest validation and version selection
    loader.py                 # safe content loading; never executes skill code
  tools/
    registry.py               # typed tool metadata
    gateway.py                # policy + authorization + execution boundary
  routing/
    router.py                 # capability and policy-aware provider selection
    budgets.py                # quota and cost enforcement
  governance/
    policy.py                 # deterministic allow/deny/review decisions
    approvals.py              # action/arguments-bound approval contract
    evidence.py                # evidence references and verification
```

The exact package layout may be adjusted to current code conventions, but responsibilities and trust boundaries must remain separate.

## 7. Data model and migration strategy

Introduce new Alembic revisions after the current head (currently revision 0028). Candidate tables:
- `ai_agent_profiles`, `ai_agent_profile_versions`
- `ai_tasks`, `ai_task_steps`, `ai_task_events`
- `ai_memory_records`, `ai_memory_links`
- `ai_skill_versions`, `ai_skill_grants`
- `ai_tool_grants`
- `ai_model_profiles`, `ai_usage_ledger`
- `ai_action_approvals`, `ai_nonce_consumptions`
- `ai_outbox_events`

Avoid creating every table in one migration. Add schema only alongside the first working vertical slice. Use UUID/string IDs consistent with current models, indexed tenant scope, database constraints for uniqueness and valid state transitions where practical, and JSON only for bounded metadata—not as a substitute for relational authorization keys.

All new organization-owned tables require organization foreign keys and tenant-scoped query tests. User-private memory must not be visible to other organization members unless explicitly shared.

## 8. API surface (versioned)

Initial internal/public API candidates:
- `POST /api/v1/ai/tasks` — create an idempotent task.
- `GET /api/v1/ai/tasks/{task_id}` — inspect task status and safe result metadata.
- `POST /api/v1/ai/tasks/{task_id}/cancel` — cancel a cancellable task.
- `GET /api/v1/ai/tasks/{task_id}/events` — paginated event/evidence timeline.
- `GET|POST /api/v1/ai/memory` — list/create authorized memory records.
- `GET|PATCH|DELETE /api/v1/ai/memory/{memory_id}` — inspect/correct/delete within retention policy.
- `GET /api/v1/ai/skills` — list approved skills and versions.
- `GET /api/v1/ai/tools` — list tools available to the current actor (never secrets).
- `GET /api/v1/ai/usage` — inspect quotas and usage for authorized roles.
- approval endpoints only after a trusted issuer and complete audit path exist.

Do not expose an unauthenticated global agent endpoint. Use existing authentication and membership dependencies, and add object-level checks for every task, memory, and run identifier.

## 9. Phased implementation and acceptance gates

### Phase 0 — contracts and schema review
- Freeze domain contracts, tenant ownership rules, state machine, idempotency semantics, and data classification.
- Review all existing AI modules and migration conventions.
- No live provider calls or production writes.

**Exit:** architecture review complete; migration and API plan agreed; no duplicate provider/security primitives.

### Phase 1 — durable task runtime
- Add task/run/step/event persistence, lease claims, idempotency, cancellation, and crash recovery.
- Add one worker process entry point and bounded mock agent.
- Add audit/outbox integration.

**Exit:** concurrent workers cannot claim the same active lease; retry does not duplicate effects; killed worker is recovered; tenant isolation tests pass.

### Phase 2 — governed tool gateway
- Add typed tool registry and deterministic capability checks.
- Ship read-only tools only.
- Add prompt-injection fixtures and cross-tenant denial tests.

**Exit:** untrusted model output cannot call unregistered tools or bypass authorization; every invocation is audited.

### Phase 3 — persistent memory and retrieval
- Add provenance-aware memory records, corrections, expiry, supersession, lexical retrieval, and citations.
- Add optional embeddings only behind a feature flag and provisioned index.

**Exit:** source provenance is preserved; user/org scopes cannot leak; deleted/expired records are excluded; retrieval returns explainable sources.

### Phase 4 — skills and model routing
- Add manifest validation, version pinning, skill grants, provider capability metadata, explicit routing, budgets, and circuit breakers.

**Exit:** skills cannot grant capabilities; sensitive data cannot silently change provider; quota and budget races are covered by PostgreSQL tests.

### Phase 5 — approvals and isolated execution
- Upgrade nonce persistence to PostgreSQL; wire trusted approval issuance; connect sandbox evidence to the runtime.
- Keep all high-risk and production operations disabled until independent review.

**Exit:** approval binds exact canonical action/arguments and tenant; replay fails under concurrency; sandbox evidence is independently verified; production deployment remains a separate service.

### Phase 6 — evaluation and staged rollout
- Add regression/evaluation suite, operational dashboards, retention controls, backup/restore and load tests.
- Enable low-risk read-only agent workflows for a limited tenant cohort.

**Exit:** measured quality, cost, latency, recovery, and isolation meet thresholds defined before rollout.

## 10. Security invariants

- No agent, model, skill, tool result, or retrieved document can modify its own grants or policy.
- Tenant identity comes from authenticated membership; every database query and tool execution re-checks scope.
- Secrets never enter prompts, memory, skill content, logs, or tool outputs.
- Retrieved content and model output are untrusted; prompt-injection detection is advisory, not authorization.
- Tool calls use validated typed arguments and narrow service functions; never shell interpolation.
- All side effects are idempotent and auditable; high-impact effects require expiring action-bound human approval.
- Missing/invalid evidence, scanner errors, unknown policy, budget exhaustion, and approval-store failures deny or quarantine.
- Sandbox has no production credentials and no default network; production deploy is a separate least-privilege system.
- Data retention, deletion, export, and user correction are first-class memory operations.
- Hashes are integrity fingerprints, HMAC is shared-key authentication, and neither is encryption or proof of human identity.

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Agent loop spends money or runs indefinitely | Hard step/tool/time/token/cost limits, leases, cancellation, budget reservations |
| Cross-tenant memory or tool access | Tenant keys, service-layer authorization, adversarial isolation tests |
| Prompt injection leads to a write | Read-only first, typed tool gateway, policy checks, approval binding |
| Duplicate execution after crash | Idempotency keys, transactional outbox, unique constraints, retry-safe handlers |
| Stale or false memory | Provenance, confidence, freshness, supersession, explicit correction |
| Provider outage or silent data transfer | Capability-aware explicit routing, circuit breaker, no silent fallback |
| False confidence from tests | Separate unit, PostgreSQL integration, sandbox-host, staging, and production evidence |
| Operational complexity too early | Modular monolith, one worker, no mandatory external queue in phase one |

## 12. Immediate implementation order

1. Review current `app/ai` contracts and all migration/test conventions against this plan.
2. Implement task/run persistence and worker lease recovery as the first vertical slice.
3. Add read-only tool gateway only after the task runtime is covered.
4. Add memory, skills, and routing incrementally; do not add an unused framework shell.
5. Keep live provider calls disabled and production deployment out of scope until their independent gates pass.

## 13. Evidence and scope notes

This plan is based on the current MUSE v3.10.0 source tree and the current Global AI Platform `master` tree. MUSE's 68 skill documents were individually fetched; core continuity/runtime files, installer, MCP server, updater, search, orchestration, selected tests, and CI configuration were inspected. The Global AI Platform's current AI modules, AI tests, primary architecture/security docs, configuration, models, and CI workflow were inspected.

No local clone was executed in this review. No MUSE test suite or platform test suite was run by this review. The latest platform commit records CI run #608 as successful; the latest MUSE commit's full CI status was not independently established here. No sandbox host, penetration test, load test, or production deployment was exercised.

This document is an architecture proposal and is not a claim that any proposed runtime, memory, queue, API, or schema is implemented.


## 14. Implementation status on the feature branch

The following foundations are implemented in `feat/ai-agent-operating-layer-design` and were CI-verified on run #670:
- durable tenant-scoped task, attempt/step, and lifecycle-event persistence; idempotent task submission; lease claims, heartbeat, expiry recovery, and a bounded registered-handler worker;
- provenance-aware user-private and organization-shared memory; source digests, confidence, sensitivity, expiry, supersession, archival, audit events, and bounded BM25-style retrieval;
- organization-owned immutable skill versions; manifest validation, SHA-256 verification on read, draft/owner-approval/disable lifecycle, and role-scoped catalog visibility;
- a static five-tool read-only gateway with strict input schemas, tenant-aware handlers, fail-closed unknown-tool handling, and audit events.

Still not implemented: continuous worker supervision, cooperative cancellation of running work, transactional outbox, PostgreSQL-backed nonce replacement, vector/embedding retrieval, memory retention and hard deletion, automated skill selection, skill-to-agent context integration, integration of the tool gateway into a model-driven agent loop, write-capable tools, model capability router, budget reservations, and full evaluation/observability pipeline. Do not describe these as complete.
