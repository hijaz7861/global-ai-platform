# HIJAZ ONE — Global Architecture, Gap Register & Migration Plan

**Product:** HIJAZ ONE — Integrated Business & Services Ecosystem  
**Repository lineage:** `hijaz7861/global-ai-platform` (repository rename is a final release step; preserve Git history)  
**Branch:** `integration/hijaz-one-platform-v2`  
**Architecture status:** Initial integration baseline; not a production-completion claim.

## 1. Product and repository decision

HIJAZ ONE is the single product and brand. The existing Global AI Platform is its core technology foundation, not a separate product. Preserve the repository, commits, security controls, migration history, tests, and reusable code. Do not delete/recreate it.

Build a modular monolith first, with independently runnable API, worker, scheduler, and optional MCP processes. Avoid premature microservices. Keep all vendor-specific integration behind replaceable adapters.

## 2. Existing Global AI Platform capabilities to reuse

The current master README and migration history report:

- FastAPI, SQLAlchemy, Alembic, SQLite local/test support, PostgreSQL production support, Docker Compose baseline.
- Identity: Argon2 passwords, email verification, signed short-lived access tokens, rotating hashed refresh tokens, replay detection, session revocation, password reset, TOTP MFA, lockout and rate limits.
- Organizations, memberships, owner/admin authorization and tenant-scoped access.
- Tenant-scoped catalog, search, currency/price filters and pagination.
- Supplier onboarding, review, procurement requests, supplier quotes, quote awards and purchase orders.
- Inventory adjustments, stock reservation, idempotent orders, cancellation and expiry.
- Double-entry accounting, chart of accounts, journal posting, reversals, periods, trial balance, account ledger, income statement, balance sheet and manual reconciliation.
- Transaction-bound audit events, privacy export and security maintenance.
- Guarded AI provider adapters and explicit provider selection.
- Optional Docker deployment baseline.

These are reusable foundations, not proof of full production readiness. Verify current source and tests before each integration.

## 3. AI operating layer integrated into master

PR #21 (`feat/ai-agent-operating-layer-design`) was merged after exact-head GitHub Actions run #737 completed successfully. The run's five jobs passed: Python 3.11, 3.12, 3.13, PostgreSQL integration, and dependency/security audit. The merge commit is `21480c93f105052ad35f343b84a6f7858de5631d`.

Merged capabilities include durable tenant-scoped AI tasks, idempotency, lease-based worker claims and recovery, cooperative cancellation, bounded single-agent execution, provenance-aware memory with digest verification, versioned approved skills, a static audited read-only tool gateway, multi-provider model routing, transactional task-event outbox, and PostgreSQL approval nonce storage.

Still explicitly missing from that PR: external worker supervisor/health service; trusted human approval issuer/audit service and HTTP release API; durable per-turn checkpoints/resumption; multi-agent planning/role orchestration; vector/embedding memory; retention/hard-delete workflow; automatic skill selection; write-capable tools/memory writes; provider token/cost reservation; provider health/circuit breaking/automatic failover; DLP; full evaluation/observability. Do not describe these as implemented.

## 4. MCP bridge PR #20

PR #20 is still a Draft. It adds a protected, read-only repository MCP bridge. CI run #611 passed and the authenticated GET health check was reported as HTTP 200. However, Streamable HTTP POST `initialize`, `tools/list`, and `tools/call` have not been verified because deployment protection intercepted the earlier unauthenticated POST. Keep this PR unmerged until an authorized POST-capable protocol test succeeds. This bridge is developer tooling, not the HIJAZ ONE business connector hub.

## 5. HIJAZ ONE domains to port into this platform

Existing Hatchable Version 40 contains these domain workflows. Treat them as migration inputs, not as proof that every workflow is production-ready:

1. Construction operations: projects, clients, BOQ/estimator, quotations, cashbook, labour and material registers.
2. Materials and supplier marketplace: supplier registration, products, approval, quote/order requests and fee review.
3. Transport and machinery: transporter profiles, vehicle/equipment listings, loader/Qingqi/tractor/dumper/truck/JCB etc., per-trip/hour/day/km/fixed-job rates, pickup/drop-off, availability, booking and document review.
4. Property marketplace: sale/rent listings, moderation, seller-declared legal status, listing fee and commission workflow.
5. Workforce and contractors: profiles, skills, age/identity review, optional GPS and hiring requests.
6. Education: student/teacher profiles, guardian consent for minors, teacher age/fee review, academy/courses/materials, moderation, paid enrolment and access gating.
7. AI customer engagement: bilingual assistant, consent-based lead capture, follow-up scheduler, opt-out, membership entitlements and specialist workflows.
8. Admin governance: approval queues, fee verification, configurable settings and audit.

### Rules to preserve

- One global identity; multiple independently paid and verified role entitlements.
- Each role has its own configurable fee, review state, permission set, limits and audit trail.
- Student registration can be free; access to a paid course requires separate verified course-specific payment.
- Approval/payment for one role never unlocks another role.
- Under-18 users may be students with guardian consent; professional roles restricted to adults remain unavailable.
- All role, tenant, ownership and payment checks are enforced server-side.
- Manual fee review must remain labelled manual until real gateway/webhook integration is implemented and tested.
- Straight-line GPS distance is not road distance or ETA.
- Seller-declared property status is not title verification or legal clearance.
- Unverified providers/listings must not be published.

## 6. Target modular monolith

```text
HIJAZ ONE
├── apps/api             FastAPI application
├── apps/web             Responsive web client
├── apps/worker          Durable AI tasks and outbox workers
├── apps/scheduler       Bounded recurring maintenance
├── apps/mcp             Optional protected MCP bridge
├── app/identity         Authentication, sessions, MFA
├── app/tenancy          Organizations, memberships, isolation
├── app/entitlements     Multi-role registry, fees, approval, limits
├── app/marketplace      Catalog, listings, search
├── app/suppliers        Supplier onboarding and procurement
├── app/orders           Orders, inventory, fulfilment
├── app/construction     Projects, BOQ, quotations
├── app/transport        Vehicles, equipment, bookings
├── app/property         Sale/rent listings and commissions
├── app/workforce        Skills, contractors, hiring
├── app/education        Students, teachers, courses, enrolments
├── app/accounting       Ledger, invoices, reconciliation
├── app/payments         Provider-neutral payment intents/webhooks
├── app/ai               Router, tasks, memory, skills, tools
├── app/notifications    Email/SMS/WhatsApp adapters
├── app/files            S3-compatible object storage adapter
├── app/audit            Audit and compliance events
├── app/platform_admin   Global governance and settings
├── migrations/
├── tests/
├── deploy/docker/
├── deploy/compose/
└── deploy/terraform/
```

This is a target map, not an instruction to mass-move files. Refactor incrementally around tested module boundaries.

## 7. Central multi-role entitlement model

Do not use a single mutable `user.role` field. Add normalized concepts:

- `role_definitions`: stable role key, age policy, fee policy, configurable limits and enabled flag.
- `user_role_entitlements`: user, role, status, verification status, payment reference, grant/expiry/suspension timestamps, reviewer and audit reference.
- `role_permissions`: explicit capability-to-role mapping.
- `role_limit_policies`: role-specific quotas and feature limits.
- `role_verification_events`: append-only review history.
- `role_fee_payments`: role-specific fee intent/reference and manual/provider verification.
- Course-specific enrolment entitlement, independent of student role.

Lifecycle: `not_requested -> pending_payment -> pending_review -> active`; exception states: `rejected`, `expired`, `suspended`, `revoked`. Free roles may bypass payment only through an explicit fee policy, never by bypassing required verification.

Every protected API must check authenticated identity, tenant, entitlement status, permission, resource ownership and applicable quota. Never trust client-supplied role, price, approval, or payment status.

**Implementation snapshot (2026-11-16, verified from GitHub and Hatchable):**

### Canonical repository and open pull requests

- **Default branch:** `master` remains at merge commit `21480c93f105052ad35f343b84a6f7858de5631d`; PR #21 (AI Agent Operating Layer) is merged. The default-branch Alembic head is `0034_postgres_approval_nonces`. This architecture document is still on PR #22's Draft branch, not on master.
- **PR #22:** this architecture/roadmap PR remains Draft, open and unmerged. It has no submitted review or inline review thread. This document update is documentation-only and uses `[skip ci]`; no CI result is claimed for it.
- **PR #20:** read-only MCP bridge remains Draft/unmerged at `f63abf1dcd5d5282aa76cfe86fb69471dd21aaf2`. Exact-head Actions run #611 passed all five jobs. Its PR description reports an authenticated GET health check as HTTP 200, but Streamable HTTP POST `initialize`, `tools/list`, and `tools/call` have not been verified. No authorized POST-capable protocol execution was available in this run; keep this PR blocked.
- **PR #23:** multi-role entitlement foundation remains Draft/unmerged. Current head is `7e2cbda99aef14932edfbbafd5a187f173d3f33e`, migration `0035_role_entitlements`. The prior exact-head Run #769 failed in all three Python matrix jobs because the new fee-audit test expected nested `changes` details that the central audit allowlist correctly discarded. PostgreSQL integration (17 passed) and dependency/Bandit audit passed. The follow-up commit changed the audit payload to explicitly allowlisted scalar before/after fields and retained the professional-role age floor on reinstatement. Exact-head GitHub Actions Run #770 completed SUCCESS on this exact head: 265 passed/17 skipped in each Python matrix job, 17 PostgreSQL integration tests passed, and all five CI jobs succeeded.
- **PR #24:** supplier catalog/seller entitlement gates remain Draft/unmerged at `7bfd25636403b017d470ab8eee9b221e7f00f077`. Exact-head Actions run #759 passed all five jobs: 260 passed/17 skipped in each Python matrix job and 17 PostgreSQL integration tests.
- **PR #25:** transport provider/vehicle listing slice remains Draft/unmerged at `a44c1731179b87c588df6e71f1858b8aad58ca05`, migration `0036_transport_marketplace`. Exact-head run #761 passed all five jobs: 263 passed/17 skipped in each Python matrix job and 17 PostgreSQL integration tests.
- **PR #26:** construction clients/projects/BOQ slice remains Draft/unmerged at `35202c34bb91ac2dc16c273ce920f8bb0b6b691d`, migration `0037_construction_operations`. Exact-head run #764 passed all five jobs: 270 passed/18 skipped in each Python matrix job and 18 PostgreSQL integration tests. The 501-row BOQ regression confirms aggregate totals include rows beyond the 500-row response cap.
- **PR #27:** construction quote slice remains Draft/unmerged at `cb659a2a9c63b40704e2f6e963736c98d19a24f3`, migration `0038_construction_quotes`. Exact-head run #766 passed all five jobs: 274 passed/18 skipped in each Python matrix job and 18 PostgreSQL integration tests.
- **PR #28:** project cashbook linked to double-entry ledger remains Draft/unmerged at `8e7968731453e1f2f020af2018111bca9f9cb4b2`, migration `0039_construction_cashbook`. Exact-head run #768 passed all five jobs: 279 passed/19 skipped in each Python matrix job and 19 PostgreSQL integration tests. PostgreSQL applied migration 0039 and `alembic check` reported no new upgrade operations. The PR description now records these exact-head results.
- **Reviews:** GitHub review and review-thread APIs returned zero submitted reviews and zero inline threads for PRs #20, #22 and #23–#28. CI success is not human approval.
- **Migration chain:** master `0034`; PR #23 `0035`; PR #24 adds no migration; PR #25 `0036`; PR #26 `0037`; PR #27 `0038`; PR #28 `0039`. The inspected revision/down-revision declarations form a linear 0034→0035→0036→0037→0038→0039 chain on their respective stacked branches.
- **Dependency drift:** current PR #23 head `7e2cbda99aef14932edfbbafd5a187f173d3f33e` is three commits ahead of the base snapshot recorded by PR #24 (`13de2075119b4863d54e7c561bbb316ee210c9c2`). PRs #24–#28 have not been refreshed against those three role-entitlement commits. Reconcile the branch chain in order #24→#25→#26→#27→#28, inspect conflicts/diffs, and verify each new exact-head CI before any owner review or merge. No dependent branch was changed in this run.

### Live Hatchable Version 40 — read-only evidence

- Hatchable reports the existing `hijaz-construction` project active at Version 40. Its latest deployment record is Version 40, status `live`, deployed `2026-11-13T07:25:10Z`, with 74 deployed file entries and 25 functions.
- Direct schema/file/function inventory confirms 46 database tables, 75 project files (including the generated virtual `AGENTS.md`), 25 active API functions and 14 SQL migrations (`0001`–`0014`).
- Direct source inspection of `api/backup.js` confirms an admin-only GET endpoint that exports 17 named tables out of 46. It exports `platform_media` metadata and storage keys, not the underlying object bytes. The 25-function registry contains no import endpoint.
- No live rows, identity documents, property deeds, message content, credentials or media bytes were exported. No Hatchable file, schema, setting, function, deployment or data was modified. The current backup endpoint is not a complete migration artifact and must not be used as one.
- The target domain `hijaz1.com` remains unconfirmed as owned. No domain purchase, paid hosting or provider activation was attempted.

## 8. Globalization gaps

- Country/subdivision registry, country-aware address and phone normalization.
- ISO 4217 currency codes; decimal-safe money (never floating point); auditable FX rate source and timestamp.
- Locale-aware dates, numbers, time zones and translation catalog. Urdu and English are initial locales, not hard-coded UI text.
- Configurable country tax/compliance hooks; no claim of legal compliance without local review.
- Provider-neutral international payments, signed/idempotent webhooks, replay protection, refunds, disputes, settlement and reconciliation.
- Logistics provider adapter; road routing/ETA only when a real provider is integrated.
- Data residency, retention, export and deletion policies by jurisdiction.

## 9. Portability contract

- Production database: standard PostgreSQL, Alembic migrations, tested backup/restore and documented upgrade path.
- Runtime: OCI Docker images; non-root user; health/readiness endpoints; graceful shutdown and bounded concurrency.
- Files: S3-compatible object storage adapter; metadata in PostgreSQL; export manifest and checksums.
- Secrets: environment variables locally and secret-manager adapter in production; never commit credentials.
- Background processing: durable database queue/outbox; independently scalable worker; idempotent handlers.
- Email, SMS, WhatsApp, payment, AI and maps: replaceable interfaces plus adapter contract tests.
- Deployment: Docker Compose for local/VPS and container hosts; cloud-specific Terraform isolated under `deploy/terraform`.
- Observability: structured logs, request IDs, metrics, traces, redaction and alerts behind provider-neutral interfaces.
- Recovery: encrypted scheduled PostgreSQL backups, object versioning where available, offsite copies and recurring restore drills.
- Migration: export users, roles, records, files, secrets metadata (never plaintext credentials), and event history; reconcile counts and financial totals before DNS cutover.

Hatchable can remain a temporary front-end/hosting environment. Do not let Hatchable-specific auth, database, functions, cron or storage become core business dependencies. Each such feature requires an explicit export/adapter plan.

## 10. Delivery phases and acceptance criteria

### Phase 0 — Inventory
- Inventory master, PRs, branches, migrations, dependencies, tests, deployment files and Hatchable schema. The read-only Hatchable Version 40 inventory is recorded in `docs/HATCHABLE_V40_INVENTORY.md`; no business data was exported or copied.
- Produce source-to-target table/API mapping and identify duplicate/contradictory rules.
- No destructive migration.

### Phase 1 — Secure foundation
- Review PR #20 and complete MCP POST protocol verification before merging.
- Add worker supervisor/health, task checkpoint/resumption, approval issuer/audit API, role orchestration, provider cost reservations and observability.
- Keep write-capable tools disabled until policy, audit, approval and sandbox tests pass.

### Phase 2 — Portable runtime
- Separate API, worker, scheduler and web build.
- PostgreSQL Compose, object storage, notification adapters, health/readiness and structured configuration.
- Verify clean install, migrations, API/worker health, backup and restore.

### Phase 3 — Identity and entitlements
- Implement central multi-role registry, fee and verification lifecycle, limits, ownership and server-side policy.
- Map existing supplier, workforce, teacher, student, transporter and contractor profiles.
- Test cross-tenant denial, cross-role denial, unpaid/pending/suspended denial, ownership, age policy and quota boundaries.

### Phase 4 — Business verticals
- Port construction, materials, supplier procurement, transport, property, workforce and education.
- Preserve approval, fee and paid-course rules.
- Add API, migration, moderation, audit and ownership tests for every vertical.

### Phase 5 — Global commerce and operations
- Payment adapter, webhook verification, idempotency, refunds, settlement, commissions and reconciliation.
- Multi-currency ledger, localization, tax configuration, email/SMS/WhatsApp adapters and logistics integration.
- Enable only providers with credentials and business setup supplied by the owner.

### Phase 6 — AI completion
- Human approval, resumable checkpoints, multi-agent orchestration, retention lifecycle, automatic skill selection, token/cost budgets, provider circuit breaker and evaluation/observability.

### Phase 7 — Release and rename
- Staging acceptance, security review, load test, restore rehearsal, portability rehearsal, migration reconciliation and operational runbooks.
- Only then rename the GitHub repository to `hijaz-one`; update remotes, docs, badges, workflows, integrations and automations.
- Configure `hijaz1.com` only after the user owns the domain and authorizes a hosting plan.

## 11. Definition of done

HIJAZ ONE is not complete until:
- Migrations work from an empty PostgreSQL database and supported upgrade path.
- Full Python matrix and PostgreSQL integration tests pass.
- No unresolved high/critical security issues.
- Every role/tenant/ownership boundary has positive and negative tests.
- Payment state cannot be forged; webhook replay and duplicates are safe.
- Every business vertical has API, ownership, moderation, audit and operations coverage.
- AI execution has bounded cost, approvals, cancellation, resumability and observability.
- Backup/restore and complete data export/import have been rehearsed.
- Same release runs in Docker Compose and at least one independent container host.
- Production secrets and external integrations are configured and end-to-end tested.
- No test, deployment, domain, payment or provider integration is claimed successful without evidence.

## 12. Current blockers

- PR #20 MCP bridge remains Draft pending authorized Streamable HTTP POST verification of `initialize`, `tools/list` and `tools/call`. Exact-head CI #611 passed, but GET health success does not prove POST protocol interoperability.
- PR #23's latest head `7e2cbda` passed exact-head Run #770 (5/5 jobs). The previous Run #769 failure was caused by the central audit allowlist discarding nested fee-change details; the follow-up emits allowlisted scalar before/after fields and is now verified by Run #770.
- PR #24–#28 are stacked on the older PR #23 base snapshot `13de207`; the current PR #23 head is three commits ahead. Refresh and reconcile the dependency chain in order, then verify exact-head CI on each updated branch. Do not merge dependent PRs independently or out of order.
- No submitted reviews or inline review threads exist for open PRs #20, #22 and #23–#28. Owner review is still required.
- The current construction slice covers clients/projects/BOQ, quotes and a project cashbook, but labour/material registers, rate-book governance, attachments and complete supplier settlement remain incomplete.
- Property, workforce/contractor hiring and education verticals still need portable implementation and role/tenant/ownership/moderation tests. Student-minor guardian consent is not implemented.
- Hatchable Version 40's admin-only backup exports 17/46 tables and no import endpoint exists. Object bytes are not included. Complete source-to-target mapping, redacted staging export, idempotent import, checksums and financial/consent/audit reconciliation have not been performed.
- The live Hatchable app remains an active Version 40 system. Do not alter, migrate or replace it until an owner-approved staging export/import and reconciliation rehearsal passes.
- PR #20's authenticated POST verification, external provider credentials, payment/webhook setup, production email/SMS/WhatsApp setup and independent portable-host rehearsal are unavailable or not authorized.
- No ownership of `hijaz1.com` is confirmed; no purchase or paid service is authorized.
- GitHub branch-protection status could not be verified through the connected API in this run (the protection endpoint returned 403). Treat branch protection/required-review policy as unverified until an authorized repository settings check is available.



## 13. Immediate engineering backlog

1. Keep PR #23 Draft and obtain owner review for the now CI-verified role foundation. If its head changes, inspect the new exact-head workflow run and logs before relying on the previous result.
2. Reconcile PR #24–#28 against the latest PR #23 head `7e2cbda` in dependency order. Inspect every resulting diff and migration head, then obtain owner review and exact-head CI for each refreshed branch.
3. Complete role-entitlement hardening: verify global/organization uniqueness, lifecycle concurrency, fee-policy audit, manual payment review, professional age gates, expiry/suspension/revocation and apply `require_active_role` to each role-protected endpoint without weakening legacy tenant checks.
4. Finish construction operations beyond the current clients/projects/BOQ/quotes/cashbook slices: labour and material registers, rate-book governance, attachments, contractor hiring, supplier settlement and customer delivery. Keep postings balanced, idempotent, tenant-scoped and auditable.
5. Port property, workforce and education from the verified Version 40 inventory. Add explicit guardian-consent workflow for student minors, separate professional age review, property-title disclaimers, moderation, ownership and tenant-negative tests.
6. Design a complete export manifest for all 46 Hatchable tables and every referenced object. Obtain owner approval for a redacted staging scope, then build idempotent import, FK ordering, conflict reporting, checksums and count/financial/consent/audit reconciliation. Do not copy live data before the rehearsal is approved and tested.
7. Complete the portable API/worker/scheduler runtime, PostgreSQL empty-install and upgrade path, S3-compatible object adapter, provider-neutral notification/payment/maps/AI interfaces, health/readiness, structured logs, secret management, backup/restore and independent-host rehearsal.
8. Complete global commerce: ISO 4217 currency metadata/exponents, decimal-safe multi-currency ledger, FX audit, tax hooks, signed/idempotent payment webhooks, refunds, disputes, settlement and reconciliation. Do not enable real providers without owner-supplied credentials and explicit setup.
9. Complete AI operating controls still missing from the merged operating layer: trusted human approval issuer/release API, durable checkpoints/resumption, bounded multi-agent orchestration, memory retention/deletion, automatic skill selection, token/cost reservations, provider circuit breaker/failover, DLP, evaluation and observability.
10. Complete release gates: security review, role/tenant/ownership negative tests, PostgreSQL migration tests, dependency audit, load checks where available, backup restore, portable-host rehearsal and operational runbooks. Rename the repository to `hijaz-one` only after all Definition of Done gates pass and the owner confirms the domain/deployment plan.


## 14. Verified engineering snapshot — 2026-11-16

This addendum supersedes older implementation snapshots above where they conflict. It is
based on the current GitHub default branch, pull-request metadata, migration source files,
workflow runs and job logs.

### Default branch and merged AI foundation

- Repository default branch remains `master`; current master head is
  `76e811d81cb42eefc68f4bafc6ed931701a91749`.
- PR #21 remains merged at `21480c93f105052ad35f343b84a6f7858de5631d`.
- PR #42 (scoped GitHub App installation token provider) is merged to master at the current
  head `76e811d81cb42eefc68f4bafc6ed931701a91749`.
- Master Alembic head is `0034_postgres_approval_nonces`.

### Governed GitHub write configuration — PR #43

- PR #43 is Draft, OPEN and unmerged. Current head:
  `bdf701d40d3621d068b8004eb11df589d708c4b0`.
- Exact-head GitHub Actions Run #823 completed SUCCESS on that SHA: 5/5 jobs passed.
  Python 3.11, 3.12 and 3.13 each reported 331 passed / 15 skipped; PostgreSQL integration
  reported 15 passed. Ruff, compilation, migration checks, dependency audit and Bandit job
  completed successfully.
- The feature remains disabled by default. Enabled configuration requires staging/production,
  PostgreSQL nonce storage, numeric bounded App/installation IDs, a parseable unencrypted
  RSA private key of at least 2048 bits, separate 32-byte HMAC keys and an explicit bounded
  owner/repository allowlist.
- No GitHub App was created or installed, no real credentials were provisioned, and no
  repository-write endpoint was enabled. A trusted approval issuer/runtime factory remains
  separate implementation work.
- Run #818 failed because a test fixture hit the production SQLite guard before the
  GitHub-write PostgreSQL guard. Run #821 exposed three expected-error-message mismatches
  after allowlist hardening. The fixture and assertions were corrected; current exact-head
  Run #823 is green. Run #822 was cancelled by the subsequent corrected commit.
- The local execution container could not resolve github.com for a clone; GitHub Actions
  Run #823 is the executable verification evidence for the current head.

### Verified open PRs, reviews and migration chain

- PR #20: Draft/unmerged; exact-head CI #611 passed, but authenticated Streamable HTTP POST
  initialize, tools/list and tools/call verification remains outstanding.
- PR #22: this architecture/roadmap PR remains Draft, OPEN and unmerged.
- PR #23: Draft/unmerged at `7e2cbda99aef14932edfbbafd5a187f173d3f33e`; exact-head Run #770
  passed. GitHub currently reports mergeable=false. Its base is the merged PR #21 commit,
  while the current master has advanced.
- PRs #24–#28 remain Draft/unmerged and are stacked in dependency order. Their recorded
  migration chain is:
  `0035_role_entitlements` → `0036_transport_marketplace` →
  `0037_construction_operations` → `0038_construction_quotes` →
  `0039_construction_cashbook`.
  Source files confirm each down_revision points to the preceding migration; PR #24 adds no
  migration. PR #24's recorded base snapshot predates the current PR #23 head, so the chain
  must be reconciled #24 → #25 → #26 → #27 → #28 before review/merge.
- GitHub review APIs returned zero submitted reviews and zero inline review threads for
  PRs #20, #22, #23–#28 and #43. No owner approval is recorded.
- GitHub's current branch endpoint reports master as `protected=false`, with protection
  disabled and no required status checks. This is a confirmed repository governance gap,
  not an unknown API state. Do not change repository settings without owner authorization.

### Release and safety state

- PR #43's configuration and documentation are implemented in its Draft branch; they are
  not merged into master.
- No dependent construction branch was rebased or rewritten in this run.
- Hatchable Version 40 remains live and untouched. Its previously verified backup inventory
  still covers only 17 of 46 tables and does not include object bytes or an import endpoint.
- No export/import, data reconciliation, backup restore, portable-host rehearsal, production
  deployment, provider activation, secret change, domain purchase or paid service is claimed.
- The repository rename remains blocked until every Definition of Done gate is demonstrably
  satisfied and the owner confirms the domain/hosting plan.


## 15. Live Hatchable inventory refresh — 2026-11-16

Read-only Hatchable connector inspection confirms the existing HIJAZ ONE project remains
active at Version 40: https://hijaz-construction.hatchable.site.

- Database schema: 46 tables.
- Deployed API functions: 25.
- Project files: 74 non-virtual files plus the generated virtual AGENTS.md manifest.
- SQL migrations: 14, numbered 0001–0014.
- Configured project environment variables returned by the project metadata API: none.
- Outstanding Hatchable agent tasks: 0.
- Latest deployment record: Version 40, status live, deployed 2026-11-13 07:25:10 UTC.

The current api/backup.js source is an admin-only GET endpoint. It selects 17 named tables
and includes only platform_media metadata/storage keys, not object bytes. The deployed
function inventory contains no import endpoint. This is not a complete migration backup and
must not be used as the sole source for migration.

No live Hatchable file, schema, data, setting, secret, deployment or task was changed during
this inspection. No data export was run. A full source-to-target mapping, redacted staging
export, idempotent import, object-byte transfer and financial/consent/audit reconciliation
remain release gates.


## 16. Open-branch freshness check — 2026-11-16

GitHub compare API confirms the following branches are behind the current master head
`76e811d81cb42eefc68f4bafc6ed931701a91749`:

| PR | Branch | Ahead | Behind | GitHub merge state |
|---|---|---:|---:|---|
| #20 | feat/render-mcp-bridge | 9 | 15 | mergeable=true, but stale base and POST verification pending |
| #22 | integration/hijaz-one-platform-v2 | 12 | 14 | Draft; docs-only updates, stale base |
| #23 | feat/hijaz-one-role-entitlements | 22 | 14 | mergeable=false, mergeable_state=dirty |
| #43 | feat/governed-github-runtime-config | 7 | 0 | Draft; exact-head CI #823 passed |

PR #24's base snapshot `13de207` is three commits behind current PR #23 head `7e2cbda`.
PRs #25–#28 are chained to the immediately preceding branch heads, but the root role branch
must be reconciled before the stack is considered merge-ready.

The review and inline-thread APIs returned zero submitted reviews and zero threads for all
13 currently open PRs (#43, #28, #22, #23, #27, #26, #24, #25, #20, #10, #4, #3 and #2).

Do not interpret a GitHub `mergeable=true` value on a stale dependent branch as approval or
release readiness. Preserve every PR as Draft until branch freshness, exact-head CI, owner
review and the applicable Definition of Done gates are satisfied.


## 17. Verified engineering snapshot — 2026-11-17

This addendum records a fresh GitHub inspection. It supersedes earlier status statements
where commit heads, CI runs, branch freshness or migration information differ.

### Canonical repository and migration chain

- The repository default branch remains `master`, at
  `76e811d81cb42eefc68f4bafc6ed931701a91749`. PR #21 and PR #42 are merged.
- The current master Alembic head is `0034_postgres_approval_nonces`, whose
  `down_revision` is `0033_ai_task_outbox`.
- Source migration headers confirm the draft vertical chain:
  `0035_role_entitlements` → `0036_transport_marketplace` →
  `0037_construction_operations` → `0038_construction_quotes` →
  `0039_construction_cashbook`. Each revision points to the preceding revision.
  PR #24 adds no migration.
- No migration from PR #23–#28 is present on master. The master database head remains 0034.

### Current implementation PRs and exact-head CI

- PR #20 remains Draft/unmerged at `f63abf1dcd5d5282aa76cfe86fb69471dd21aaf2`.
  Exact-head run #611 passed. Authenticated Streamable HTTP POST
  `initialize`, `tools/list` and `tools/call` remain unverified.
- PR #22 (this architecture PR) remains Draft/unmerged. Before this documentation
  refresh its branch compared as 13 commits ahead and 14 behind master. It is
  documentation/inventory work only; it must be reconciled against current master
  before review and is not a release-ready branch.
- PR #23 multi-role entitlements remains Draft/unmerged at
  `7e2cbda99aef14932edfbbafd5a187f173d3f33e`; exact-head run #770 passed all
  five jobs. GitHub reports mergeable=false. Its branch is 22 ahead/14 behind
  master. PR #24 is based on older commit `13de2075119b4863d54e7c561bbb316ee210c9c2`;
  the current PR #23 branch is three commits ahead of that snapshot.
- PR #24 supplier gates remains Draft at `7bfd25636403b017d470ab8eee9b221e7f00f077`;
  exact-head run #759 passed.
- PR #25 transport listings remains Draft at `a44c1731179b87c588df6e71f1858b8aad58ca05`;
  exact-head run #761 passed.
- PR #26 construction projects/BOQ remains Draft at
  `35202c34bb91ac2dc16c273ce920f8bb0b6b691d`; exact-head run #764 passed.
- PR #27 construction quotes remains Draft at
  `cb659a2a9c63b40704e2f6e963736c98d19a24f3`; exact-head run #766 passed.
- PR #28 construction cashbook remains Draft at
  `8e7968731453e1f2f020af2018111bca9f9cb4b2`; exact-head run #768 passed.
  Preserve dependency order #23 → #24 → #25 → #26 → #27 → #28. The successful
  historical runs do not remove the stale-base requirement.
- PR #43 governed GitHub runtime configuration remains Draft at
  `bdf701d40d3621d068b8004eb11df589d708c4b0`; exact-head run #823 passed.
- PR #44 authenticated repository approval remains Draft at
  `7824f98c06a51c875692a7e467c53959605483a3`; exact-head run #838 passed.
  It is stacked on PR #43.
- PR #48 durable sandbox-validation task remains Draft at
  `94c78ee018e13f71b8415a6b673ba98b3bd04197`; exact-head run #849 passed.
- PR #49 bounded read-only proposal generation remains Draft at
  `a06f74fb0e2e9aa927beaa4a4e6721f5b8a9940b`; exact-head run #851 passed.
- PR #50 repository/base-bound sandbox attestation remains Draft at
  `7d97584efdb0d8cdada9b807655afb7e10da51af`; exact-head run #856 passed.
  PRs #48–#50 are stacked on #44 → #48 → #49 respectively; review dependencies
  from the bottom upward and refresh exact-head CI after any rebase.
- PR #51 PostgreSQL-backed provider circuit breaker remains Draft at
  `0207cf1c44adb2b82bdb3550a950aac7e2be2689`; exact-head run #862 passed all
  five jobs. It is an opt-in draft feature, not part of master.
- PR #52's agent-only secret guard was closed unmerged as superseded after its
  functionality was carried forward and extended into PR #53. Its prior exact-head
  run #863 had passed on `ab542c506a56eec119a788f075c08d39dde07729`.
- PR #53 secret-pattern egress guard remains Draft at
  `774fe99cba9920c6d18529b5b07f19c38142eab7`. Exact-head run #866 completed
  SUCCESS on this SHA: all five jobs passed; Python 3.11, 3.12 and 3.13 each
  reported 326 passed / 15 skipped; PostgreSQL integration reported 15 passed;
  Ruff, compilation, Alembic migration checks, dependency audit and Bandit passed.
  The guard covers durable agent prompts and the regular organization AI chat
  endpoint before quota reservation/provider egress. It remains narrow pattern-
  based detection, not comprehensive DLP.

GitHub review and inline-thread APIs returned zero submitted reviews and zero
inline threads for PRs #20, #22, #23, #43, #44, and #48–#53 in this inspection.
CI success is not owner approval.

### Hatchable and release safety

The last read-only Hatchable Version 40 inventory was recorded on 2026-11-16:
46 tables, 25 API functions, 74 non-virtual files plus the generated AGENTS.md,
14 SQL migrations, and an admin-only backup that exports 17/46 tables without
object bytes or an import endpoint. This inventory was not refreshed in this run.
No live Hatchable export, file/schema change, data copy, deployment, payment or
communication provider activation, secret change, domain purchase, or paid
service was performed.

### Immediate safe next work

1. Keep PRs #20, #22–#28, #43–#52 Draft until owner review and exact-head checks.
2. Reconcile the role/vertical dependency chain, beginning with PR #23's stale,
   currently non-mergeable base; do not independently refresh or merge downstream
   verticals out of order.
3. Keep PR #53 Draft pending owner review; continue broader DLP, provider-health
   administration, durable checkpoints/resumption and evaluation.
4. Continue portable runtime, property/workforce/education, global commerce,
   complete export/import, backup/restore and independent-host acceptance work.
5. Preserve the live Hatchable app and current repository identity until every
   documented Definition of Done gate has been rehearsed and owner-approved.


## 18. Verified engineering snapshot — 2026-11-18

This addendum records the current GitHub state checked for this run. It supersedes
older commit, PR, workflow and branch-freshness statements above where they differ.

### Canonical repository and migrations

- Repository default branch is `master`; latest commit search returned
  `76e811d81cb42eefc68f4bafc6ed931701a91749`.
- Master contains migration `0034_postgres_approval_nonces`, whose
  `down_revision` is `0033_ai_task_outbox`. Migration `0035_role_entitlements`
  is absent from master.
- Draft migration headers were re-read from their respective branches:
  `0035_role_entitlements` → `0036_transport_marketplace` →
  `0037_construction_operations` → `0038_construction_quotes` →
  `0039_construction_cashbook`. Each declared down-revision points to the
  preceding revision; PR #24 adds no migration. This is a source-level branch
  chain check, not evidence that these migrations are installed on master.
- The canonical master does not currently contain
  `docs/HIJAZ_ONE_GLOBAL_ARCHITECTURE.md`; that document exists on this Draft
  architecture branch.

### Open PR and exact-head workflow snapshot

The repository currently has 19 open PRs. Relevant current states:

- PR #20 read-only MCP bridge remains Draft/unmerged at
  `f63abf1dcd5d5282aa76cfe86fb69471dd21aaf2`; exact-head Run #611 succeeded.
  Authenticated Streamable HTTP POST `initialize`, `tools/list` and
  `tools/call` are still unverified. The prior unauthenticated POST was stopped
  by Vercel SSO before the app, so it is neither protocol success nor protocol
  failure.
- PR #22 (this architecture PR) remains Draft/unmerged at
  `839db8a03c020dda8516970ff19384c5a60dba65`. It compares 16 commits ahead
  and 14 behind current master, with merge base
  `21480c93f105052ad35f343b84a6f7858de5631d`. It has no workflow run for its
  latest documentation-only head and no submitted review or inline thread.
- PR #23 multi-role foundation remains Draft/unmerged at
  `7e2cbda99aef14932edfbbafd5a187f173d3f33e`; exact-head Run #770 succeeded
  (265 passed/17 skipped in each Python matrix job; 17 PostgreSQL tests).
  GitHub reports `mergeable=false`; compare shows 22 ahead/14 behind master.
- PRs #24–#28 remain Draft/unmerged and their exact-head runs #759, #761,
  #764, #766 and #768 respectively succeeded. They remain stacked on older
  dependency snapshots; preserve order #23 → #24 → #25 → #26 → #27 → #28.
  Their historical green runs do not prove the current dependency stack is
  reconciled.
- PR #43 governed GitHub configuration remains Draft at
  `bdf701d40d3621d068b8004eb11df589d708c4b0`; exact-head Run #823 succeeded.
  PR #44 authenticated approval runtime remains Draft at
  `7824f98c06a51c875692a7e467c53959605483a3`; exact-head Run #838 succeeded.
  PRs #48, #49 and #50 remain stacked on #44 in order and their exact-head
  runs #849, #851 and #856 succeeded. PR #51 provider circuit breaker remains
  Draft at `0207cf1c44adb2b82bdb3550a950aac7e2be2689`; Run #862 succeeded.
- PR #53 outbound secret-pattern guard is Draft/unmerged at
  `c7dd1d8f3e0c0318c0c33eb14c95eab266d1f560`. Exact-head Run #868 succeeded:
  5/5 jobs; Python 3.11–3.13 each 337 passed/15 skipped; PostgreSQL 15 passed;
  lint, compilation, Alembic checks, dependency audit and Bandit passed. It now
  detects high-confidence OpenAI, Anthropic, OpenRouter, Groq, Hugging Face,
  GitLab, npm, PyPI and Azure Storage patterns in addition to the earlier
  credential patterns. It remains narrow pattern-based blocking, not
  comprehensive DLP.
- PR #54 rolling AI chat token-budget enforcement is Draft/unmerged at
  `b8eb5840f7f1301790f8db538c21c63153dd115f`. Exact-head Run #869 succeeded:
  5/5 jobs; Python 3.11–3.13 each 312 passed/15 skipped; PostgreSQL 15 passed;
  lint, compilation, Alembic checks, dependency audit and Bandit passed. It
  brings the existing `AI_DAILY_TOKEN_LIMIT` reservation logic to regular
  organization chat and records a conservative UTF-8-byte-plus-output estimate
  in the append-only audit event. This is not tokenizer-derived or billed usage.

GitHub review and inline-thread APIs returned zero submitted reviews and zero
inline threads for the currently open PRs inspected, including #20, #22–#28,
#43–#54, #10, #4, #3 and #2. CI success is not owner approval.

### Remaining release blockers and safety

- PR #23 is the root role/vertical dependency blocker: it is stale and
  non-mergeable against current master. No rebase, force-push, merge or
  downstream branch rewrite was performed.
- MCP authenticated POST verification remains unavailable through the
  currently evidenced access path.
- The last read-only Hatchable Version 40 inventory is dated 2026-11-16:
  46 tables, 25 functions, 74 non-virtual files plus generated AGENTS.md,
  14 migrations; admin backup covers only 17/46 tables and does not include
  object bytes or an import endpoint. No newer inventory or data export was
  performed in this run.
- No live Hatchable changes, data export/import, migration, production
  deployment, provider activation, secret change, paid service, domain
  purchase or repository rename was performed.
- Owner review, branch protection/required-check configuration, complete
  migration rehearsals, portable-host rehearsal, backup/restore, global
  commerce, property/workforce/education verticals, guardian consent, complete
  AI governance and full DLP remain open Definition of Done gates.

### Next safe engineering sequence

1. Reconcile the PR #23 entitlement foundation against current master in a
   reviewed branch without force-updating its existing history; rerun exact-head
   migration, SQLite/PostgreSQL and role/tenant-negative tests.
2. Refresh dependent PRs #24–#28 in dependency order only after the role
   foundation is reconciled.
3. Complete authenticated MCP POST protocol verification through an authorized
   POST-capable access path.
4. Continue durable AI checkpoints/resumption, approval governance, cost
   reservations, DLP evaluation and provider administration.
5. Build complete Hatchable source-to-target mapping and staged export/import
   rehearsal before any data movement; keep the live app untouched.


## 19. Role-entitlement concurrency review — 2026-11-18

A source review of PR #23 identified that administrator review and lifecycle
handlers previously loaded entitlement/role rows without row-level locking.
Concurrent administrators could therefore validate the same stale pending
state and write conflicting transitions. The Draft branch now uses PostgreSQL
`SELECT ... FOR UPDATE` for role definitions during application/configuration/
approval/reinstatement and for entitlement rows during review and lifecycle
transitions. A regression test compiles both lock queries with the PostgreSQL
dialect and asserts the generated SQL includes `FOR UPDATE`.

- Latest PR #23 head is now
  `63b037cfc13ad2d121ca556934f6d2dbe44e6011` (26 commits ahead / 14 behind
  master; merge base remains PR #21 merge commit
  `21480c93f105052ad35f343b84a6f7858de5631d`).
- PR #23 remains Draft and GitHub reports `mergeable=false`.
- PR #24's supplier branch remains based on the older role snapshot. Compare
  between the current role branch and supplier branch is diverged (7 commits
  ahead / 4 behind relative to the supplier branch), so the downstream stack
  cannot be refreshed by a simple fast-forward.
- Exact-head workflow lookup and combined commit status returned no workflow
  run/status for the new code head or the documentation-updated PR head.
  Previous Run #770 passed only on the earlier head
  `7e2cbda99aef14932edfbbafd5a187f173d3f33e`; it does not verify these changes.
- The local container cannot resolve `github.com`, so a repository clone and
  local project test run were not possible. A focused local SQLAlchemy compile
  check confirmed that both PostgreSQL-dialect statements render `FOR UPDATE`.

No branch rebase, force-push, merge, deployment or live-data change was made.
The row-lock hardening remains unverified by exact-head CI and requires
PostgreSQL integration plus owner review before it can be considered complete.


## 20. Portable worker configuration audit — 2026-11-18

The current master Compose file already defines a separate PostgreSQL-backed
`worker` service and an API health dependency. It does not define a scheduler
service. `app/maintenance.py` is a bounded one-shot maintenance command, not a
durable scheduler with persisted next-run state, leases and missed-run recovery.

A configuration defect was found: the API container received
`AI_DAILY_TOKEN_LIMIT` and `AI_AUTO_FAILOVER_ENABLED`, but the worker container
did not. That meant durable agent calls could use different budget/failover
settings from regular API chat in the same Compose deployment. Draft PR #55
adds both variables to the worker environment and a regression check comparing
the API and worker service sections.

- PR #55 head: `021d00d69d9ae29fab50aa263ad8dbadf8a8bc09`; Draft/unmerged.
- Exact-head GitHub Actions Run #870 is queued, not yet passed.
- No production Compose file was changed; this is a Draft source change only.

The durable scheduler, persisted schedule/lease state, missed-run recovery,
scheduler observability, backup/restore rehearsal and portable-host deployment
remain unimplemented release gates. Do not describe the existing one-shot
maintenance command as a completed scheduler.


### PR #55 exact-head CI result — 2026-11-18

The queued run completed. PR #55 head
`021d00d69d9ae29fab50aa263ad8dbadf8a8bc09` passed exact-head GitHub Actions
[Run #870](https://github.com/hijaz7861/global-ai-platform/actions/runs/44370233178):
all 5 jobs succeeded; Python 3.11–3.13 each reported 311 passed/15 skipped;
PostgreSQL integration reported 15 passed; lint, compilation, Alembic checks,
dependency audit and Bandit passed. PR #55 remains Draft/unmerged with no
submitted review or inline review thread.


## 21. End-of-run repository inventory — 2026-11-18

A final repository refresh after the Draft PRs in this run confirms:

- Default branch remains `master`; the verified master SHA remains
  `76e811d81cb42eefc68f4bafc6ed931701a91749`.
- There are now 21 open PRs and 58 branches. The two new feature branches
  `feat/ai-chat-token-budget` and `fix/worker-ai-budget-env-parity` are
  represented by Draft PRs #54 and #55.
- This architecture branch's latest head is
  `7a078dab19c7750d101b3796b8ebf9b44ca14756`; compare to master is 20 ahead /
  14 behind, merge base still `21480c93f105052ad35f343b84a6f7858de5631d`.
- PR #23 latest head remains
  `63b037cfc13ad2d121ca556934f6d2dbe44e6011`, 26 ahead / 14 behind and
  `mergeable=false`. PR #24 supplier branch is 4 commits ahead of and 7
  commits behind the current role branch, so both branches contain unique work.
- PR #53 Run #868 and PR #54 Run #869 succeeded 5/5 jobs on their exact heads.
  PR #55 Run #870 succeeded 5/5 jobs on its exact head. PR #23 has no workflow
  run or combined status for its new head in the connected GitHub status
  lookup; PR #22's documentation-only commits were marked `[skip ci]`.
- No reviewed PR gained an owner review or inline review thread during this
  run. All new implementation PRs remain Draft/unmerged.

This is a repository snapshot only; it does not satisfy the global release
Definition of Done.


## 22. Read-only Hatchable Version 40 refresh — 2026-11-18

The connected Hatchable metadata/schema/file/function APIs were used read-only.
The existing live project remains active at
`https://hijaz-construction.hatchable.site`, current Version 40. Its latest
listed deployment is Version 40, deployed 2026-11-13 07:25:10 UTC.

Current inventory:

- 46 PostgreSQL tables and their column/index metadata.
- 25 active API functions/routes, covering assistant/admin, backup, education,
  engagement, follow-up, management, marketplace, property, provider workspace,
  suppliers, transport and media upload.
- 75 files in the project manifest, including 25 API files, 14 SQL migrations,
  public pages/assets and the generated `AGENTS.md`.
- 14 migrations, `0001` through `0014`.
- One active scheduled function, `POST /api/jobs/follow-up`, declared hourly
  (`0 * * * *`). The scheduler reports 136 runs in the last 7 days, zero
  5xx errors in that window, last fired at 2026-11-18 18:05 UTC and next fire
  at 19:05 UTC. This is the live Hatchable scheduler, not the portable
  platform's missing scheduler.

### Backup/import boundary

The current `api/backup.js` was read without executing it. It declares
`access = 'admin'` and `methods = ['GET']`. It exports 17 table datasets:
clients, projects, transactions, labour entries, material entries, pricing
items, quotes, audit events, suppliers, supplier products, quote requests,
workforce profiles, service requests, material orders, platform reviews,
platform media metadata and platform fee settings. The media export contains
metadata/storage keys, not object bytes. The file does not provide an import
endpoint. This is not a complete backup of all 46 tables and cannot be treated
as a migration-ready export.

No backup endpoint was executed, no row data or media bytes were read, and no
file, schema, record, environment variable, deployment or live workflow was
changed. The staged export/import and reconciliation design remains a release
gate. Keep the current live Version 40 untouched.


## 23. Verified repository, AI-control and governance snapshot — 2026-11-19

### Canonical repository and migration state

- Default branch is `master`, verified at `72d20700116e5ca7ef76c5d303a0ceb824e0c217`. A direct compare of `master` with this SHA returned `identical`.
- The current master Alembic head remains `0034_postgres_approval_nonces`. Migration `0035_role_entitlements` is not present on master; a master-path lookup returned 404. Draft role/vertical revisions `0035`–`0039` remain outside master.
- Current repository inventory: 18 open PRs and 63 branches.
- The master commit `973f55de96e01ddc64e209ba4a4dd468153c053b` added the Cloudflare Free edge Worker source and deployment record. That record describes a read-only status/health façade with D1 and KV; it explicitly does not host the FastAPI application or Qwen3-8B. External Cloudflare resource state was not revalidated in this run, and no deployment or configuration change was made here.

### AI control slices now present on master

The following PRs are now reported by GitHub as merged. Their exact-head CI evidence was checked:

- PR #53 secret-pattern egress guard, merge commit `f4701c89cb704dfdb24a4a2904fb5b08bacc9aad`; Run #868 passed 5/5 jobs (Python 3.11–3.13: 337 passed/15 skipped each; PostgreSQL: 15 passed). This remains narrow pattern-based blocking, not comprehensive DLP.
- PR #55 worker AI budget/failover environment parity, merge commit `53cf8bf025ef549e2036e24ad8ac138691067a68`; Run #870 passed 5/5 jobs (311 passed/15 skipped each Python version; PostgreSQL: 15 passed).
- PR #57 governed memory purge and expiry retention, merge commit `6efd39840f9192b213c0f655e5763eaf5717fc6b`; exact-head Run #879 passed 5/5 jobs (314 passed/16 skipped each Python version; PostgreSQL: 16 passed). The API preserves archive-only `DELETE /{memory_id}`; a separate purge route physically deletes caller-owned private memory, while organization-shared memory requires the organization owner. Explicitly expired records are purged in bounded batches by the one-shot maintenance command, with allowlisted metadata-only audit details. No default TTL is imposed, and the portable durable scheduler is still missing.
- PR #59 rolling organization chat token budget, merge commit `72d20700116e5ca7ef76c5d303a0ceb824e0c217`; exact-head Run #889 passed 5/5 jobs (346 passed/16 skipped each Python version; PostgreSQL: 16 passed). The chat estimate is UTF-8 prompt bytes plus maximum output tokens, not tokenizer-derived or billed usage.

GitHub now reports PRs #53, #55, #57 and #59 as merged. This scheduled engineering run did not invoke the merge action. The connected PR metadata did not expose `merged_by`; do not attribute these merges to a specific actor from the available evidence. Review and inline-thread APIs returned zero submitted reviews and zero threads for PRs #57 and #59 at inspection time. This is a governance concern because the owner's standing rule is to keep work Draft until owner review. Branch protection/required-review enforcement remains unverified; a previous branch-protection lookup was denied by GitHub. Do not assume that CI success alone authorizes a merge.

PR #54 remains Draft/open at `b8eb5840f7f1301790f8db538c21c63153dd115f` with successful Run #869, but its chat token-budget implementation is superseded by the merged PR #59 branch. It is a stale duplicate candidate; it has not been closed or altered in this run.

### Remaining dependency blockers

- PR #20 remains Draft/open at `f63abf1dcd5d5282aa76cfe86fb69471dd21aaf2`; Run #611 passed 5/5 jobs, but authenticated Streamable HTTP POST verification for `initialize`, `tools/list`, and `tools/call` remains outstanding.
- PR #22 remains Draft/open. Before this documentation refresh its head was `a35d5399a09b9fa51d6902a207f94514c5fa6aa9`; compare to current master was 22 ahead/20 behind with merge base `21480c93f105052ad35f343b84a6f7858de5631d`. This document update is documentation-only and uses `[skip ci]`; it does not reconcile the branch.
- PR #23 remains Draft/open at `63b037cfc13ad2d121ca556934f6d2dbe44e6011`, 26 ahead/20 behind master and `mergeable=false`. Exact-head workflow and combined status lookups returned no run/status; Run #770 applies only to an earlier head. No rebase, force-push or merge was performed.
- PR #24 remains based on the older role snapshot. The supplier branch is diverged from the current role branch (4 commits ahead / 7 behind); refresh PRs #24–#28 in dependency order only after a safe role-foundation reconciliation.
- PRs #43–#51 retain their previously verified Draft implementation slices and exact-head CI evidence; owner review and integration reconciliation remain release gates.

### Live data and release boundaries

- The last read-only Hatchable Version 40 inventory is dated 2026-11-18: 46 tables, 25 active API functions, 75 files and 14 migrations. Its admin-only backup covers 17 table datasets and media metadata, not object bytes; no import endpoint was found.
- No Hatchable export/import, row-data copy, live application edit, production deployment, provider activation, secret change, paid service, domain purchase or repository rename was performed in this run.
- The full Definition of Done remains open: role/tenant lifecycle and branch reconciliation, authenticated MCP POST verification, all verticals, portable durable scheduler/worker, complete commerce and payment reconciliation, complete AI governance/DLP/evaluation, full backup/restore and staged data reconciliation, owner review, branch protection and portable-host rehearsal.

### Next safe engineering sequence

1. Reconcile PR #23 against current master without rewriting its existing history; rerun exact-head SQLite/PostgreSQL migration and role/tenant negative tests.
2. Refresh PRs #24–#28 in dependency order after the role foundation is reconciled.
3. Keep the now-merged memory expiry routine explicitly one-shot until a persisted, leased, missed-run-safe portable scheduler is implemented and tested.
4. Complete authenticated MCP POST protocol verification through an authorized POST-capable path.
5. Continue the remaining property, workforce, education, global commerce, AI checkpoint/resumption, retention/export and release-rehearsal work.

This snapshot records GitHub evidence; it is not a production-readiness or Definition-of-Done claim.

## 24. Verified repository and scheduler snapshot — 2026-11-19

### Canonical master and migration state

- GitHub compare confirms current default branch master is exactly 5add4c1cb6c54b696620d3f3d28ca8708ca6a4cd, the merge commit for PR #60. The current master Alembic head remains 0034_postgres_approval_nonces; PRs #56–#60 added no migration. The role/vertical chain 0035–0039 remains on unmerged branches.
- Repository inventory: 18 open PRs and 64 branches. Review and inline-thread APIs returned zero submitted reviews and zero threads for all 18 open PRs, including new PR #61. This is not owner approval.
- PR #22 remains Draft/open. At the start of this snapshot refresh its branch was 24 ahead / 21 behind master, with merge base 21480c93f105052ad35f343b84a6f7858de5631d. This documentation-only update adds another commit; re-fetch the branch pointer and divergence from GitHub before integration. GitHub reported mergeable=true, but the branch remains diverged and has no owner review; do not treat it as release-ready.
- PR #23 remains Draft/open at 63b037cfc13ad2d121ca556934f6d2dbe44e6011, 26 ahead / 21 behind master and mergeable=false. The exact-head workflow lookup returned no run; the historical Run #770 applies only to an earlier SHA.
- PR #20 remains Draft/open at f63abf1dcd5d5282aa76cfe86fb69471dd21aaf2; Run #611 passed, but authenticated Streamable HTTP POST initialize, tools/list, and tools/call remain unverified.
- PRs #24–#28 remain a stacked role/supplier/transport/construction dependency chain. Their historical exact-head CI passed, but their base snapshots must be reconciled after the role foundation. PR #43 remains Draft and stale/non-mergeable; PRs #44 and #48–#50 remain stacked governed GitHub/sandbox work. All are unreviewed.

### AI-control and runtime work now merged

GitHub reports these PRs merged, and the exact PR-head workflow runs inspected for this snapshot completed successfully:

- PR #53 outbound secret-pattern guard — merge commit f4701c89cb704dfdb24a4a2904fb5b08bacc9aad, Run #868 passed 5/5 jobs. It remains narrow pattern-based blocking, not comprehensive DLP.
- PR #55 worker token-budget/failover environment parity — merge commit 53cf8bf025ef549e2036e24ad8ac138691067a68, Run #870 passed 5/5 jobs.
- PR #56 Cloudflare edge health/status slice — merge commit 973f55de96e01ddc64e209ba4a4dd468153c053b, Run #44585055435 passed 5/5 jobs. Its PR description reports a read-only Worker with D1/KV; external Cloudflare resource state was not revalidated in this run.
- PR #57 governed memory purge/expiry — merge commit 6efd39840f9192b213c0f655e5763eaf5717fc6b, Run #44588798825 passed 5/5 jobs. The purge remains explicit/owner-gated for shared memory; no default TTL is imposed.
- PR #58 PostgreSQL URL normalization for psycopg 3 — merge commit d6543e63fb8902cdcaaf0452bcf31fefe89f9e0e, Run #44588600768 passed 5/5 jobs.
- PR #59 rolling organization chat token budget — merge commit 72d20700116e5ca7ef76c5d303a0ceb824e0c217, Run #44589349123 passed 5/5 jobs. Its quota estimate is UTF-8 prompt bytes plus maximum output tokens, not tokenizer-derived or billed usage.
- PR #60 PostgreSQL provider circuit breaker/failover — merge commit 5add4c1cb6c54b696620d3f3d28ca8708ca6a4cd, Run #44590149295 passed 5/5 jobs. It is disabled by default and covers the durable agent-runner path; regular chat circuit integration and an operational health API remain gaps.

PR #54 was closed unmerged as superseded by PR #59. This run did not invoke any merge, deployment, provider activation or Cloudflare configuration action. The GitHub metadata used here does not establish who performed the merges.

### Durable maintenance scheduler — Draft PR #61

- New Draft PR #61: feat(runtime): durable database-coordinated maintenance scheduler, branch feat/durable-maintenance-scheduler, final verified head 61ba169515e92bad87e28431b149ca7cb0a11e0d, directly based on current master 5add4c1.
- It adds a separate Compose scheduler, bounded interval/poll/batch settings, a durable checkpoint using the existing committed platform.maintenance_completed audit event, a PostgreSQL session-level advisory lock held on the same connection as the maintenance sessions, one-run missed-interval recovery, and count-only logs. Connection loss aborts the work and releases the lock together. No migration is introduced, avoiding a collision with the unmerged role migration 0035.
- SQLite is limited to a single local scheduler process; production configuration requires PostgreSQL.
- Exact-head Actions Run #905 on 61ba169515e92bad87e28431b149ca7cb0a11e0d completed successfully: 5/5 jobs passed. Each Python 3.11/3.12/3.13 job reported 369 passed, 18 skipped and 73 warnings; PostgreSQL integration reported 18 passed and 3 warnings. Ruff, compilation, Alembic upgrade/check, dependency audit and Bandit succeeded. The local clone/test attempt was blocked because the execution environment could not resolve github.com.
- PR #61 remains Draft, open and unmerged; no reviews or inline threads exist.

### Hatchable and release boundaries

The last read-only Hatchable Version 40 inventory remains the 2026-11-18 snapshot: 46 tables, 25 API functions, 75 files and 14 SQL migrations. The admin-only backup covers only 17 table datasets and media metadata, not object bytes, and no import endpoint was found. No export was run and no live Hatchable record, file, schema, deployment or environment was changed.

The Definition of Done remains open: role/tenant reconciliation, authenticated MCP POST verification, property/workforce/education verticals, portable backup/restore and data reconciliation, full commerce/payment settlement, AI checkpoints/resumption and comprehensive DLP/evaluation, owner review, branch protection and independent-host rehearsal.

### Next safe engineering sequence

1. Finish exact-head CI and code review for PR #61; keep it Draft until the scheduler lock/checkpoint behavior is verified.
2. Reconcile the role-entitlement foundation against current master without force-pushing or rewriting the historical branch, then refresh PRs #24–#28 in dependency order.
3. Obtain an authorized POST-capable MCP test path for PR #20.
4. Continue property, workforce, education, commerce and full backup/import/reconciliation implementation.

This snapshot is evidence tracking, not production approval or a Definition-of-Done claim.


## 25. Verified engineering continuation — 2026-11-20

### Repository and dependency snapshot

- GitHub repository metadata confirms `hijaz7861/global-ai-platform`, default branch `master`, public visibility, and connected integration push/admin permissions. The latest verified master base SHA used by the current independent PRs is `5add4c1cb6c54b696620d3f3d28ca8708ca6a4cd`.
- Current inventory before opening PR #62: 18 open PRs and 64 branches; after PR #62 was opened, the verified inventory is 19 open PRs and 65 branches. PR #22 remains Draft/open/unmerged at `0ebbb64cfee2d0b4d47cba1a886a40cb4b9466b4`; after this documentation update, compare against master is 27 ahead / 21 behind with merge base `21480c93f105052ad35f343b84a6f7858de5631d`. GitHub reports mergeable=true, but this branch is materially diverged and has no owner review; do not treat it as ready to merge.
- PR #23 remains Draft/open/unmerged at `63b037cfc13ad2d121ca556934f6d2dbe44e6011`; compare is 26 ahead / 21 behind and GitHub reports mergeable=false. The old Run #770 applies only to its historical earlier head; no exact-head CI has been confirmed for the concurrency-hardening head.
- PR #61 remains Draft/open/unmerged at `61ba169515e92bad87e28431b149ca7cb0a11e0d`, based directly on master. Exact-head Run #905 completed SUCCESS (5/5 jobs): 369 passed/18 skipped on each Python 3.11–3.13 job; PostgreSQL integration 18 passed; lint, compilation, Alembic, dependency audit and Bandit passed. Review APIs returned zero reviews and zero inline threads. The PR is mergeable but remains unreviewed and not approved.
- The master Alembic head remains `0034_postgres_approval_nonces`; the role/vertical migration chain `0035`–`0039` remains on unmerged branches.

### Portable object-storage adapter — Draft PR #62

- Draft PR #62 adds `app/storage.py` with a provider-neutral `ObjectStorage` protocol, atomic local filesystem adapter, and injected-client S3-compatible adapter; it adds `requirements-storage.txt`, `docs/OBJECT_STORAGE.md`, and `tests/test_object_storage.py`.
- The adapter enforces relative POSIX keys, rejects traversal/dot/empty/backslash/NUL segments, bounds keys to 1,024 UTF-8 bytes and object sizes to a configurable ceiling, validates content-type header characters, rejects resolved local symlink escapes, and propagates non-404 provider errors.
- S3 client creation is explicit and lazy. No provider endpoint, bucket, credentials, account, or external network service was configured or contacted.
- The adapter is not yet wired to business upload routes. Tenant/object authorization, malware scanning, signed URLs, encryption, retention, audit integration, media migration, backups and restore remain open release gates.
- Branch `feat/s3-compatible-object-storage-adapter` is exactly 5 commits ahead / 0 behind master, based on `5add4c1cb6c54b696620d3f3d28ca8708ca6a4cd`. PR #62 is Draft/open/unmerged and GitHub reports mergeable=true. Exact-head `743ef9942ebfa6369b232f7825ce5684f9063b7f` triggered Actions Run #906, which was still in progress at snapshot time; no CI pass is claimed. Review and inline-thread APIs returned zero entries.
- The full diff was inspected across four files. Local tests could not be run in this environment; GitHub Actions is the required verification path.

### Hatchable and safety boundary

The last verified Hatchable Version 40 inventory remains the 2026-11-18 read-only snapshot (46 tables, 25 active API functions, 75 files, 14 SQL migrations). Its inspected admin-only backup exports 17 table datasets and media metadata, not object bytes; no import endpoint was found. No backup/export/import or live Hatchable mutation was performed.

No PR was merged, no production deployment or secret/provider activation occurred, no data was deleted or migrated, and no domain or paid service was purchased. HIJAZ ONE Definition of Done remains open.

### Next safe work

1. Inspect Run #906 and all five jobs on the exact PR #62 head; correct any test/lint/security findings before further work.
2. Reconcile PR #23's role changes onto current master in a new integration branch without force-pushing or rewriting history; inspect every conflicting file and refresh PRs #24–#28 in dependency order.
3. Keep PR #61 Draft until an owner review and staging operational rehearsal verify scheduler behavior.
4. Continue storage service integration only after tenant-scoped upload/download authorization, content scanning, signed access, retention and backup/restore contracts are implemented.
5. Obtain an authorized authenticated Streamable HTTP POST test path for PR #20 and continue property/workforce/education/commerce and data reconciliation work.

This snapshot is evidence tracking, not owner approval, release readiness, production deployment, or a Definition-of-Done completion claim.


### Post-PR #62 inventory correction — 2026-11-20

After creating Draft PR #62, GitHub reports 19 open PRs and 65 branches (the prior 18/64 count was the pre-creation snapshot). The architecture branch `integration/hijaz-one-platform-v2` is now 27 commits ahead / 21 behind master, merge base `21480c93f105052ad35f343b84a6f7858de5631d`; its current head is `0ebbb64cfee2d0b4d47cba1a886a40cb4b9466b4`. PR #62 remains exactly 5 commits ahead / 0 behind master. Actions Run #906 completed SUCCESS (5/5 jobs) on exact head `743ef9942ebfa6369b232f7825ce5684f9063b7f`: Python 3.11/3.12/3.13 each 375 passed / 17 skipped / 73 warnings; PostgreSQL integration 17 passed / 3 warnings; dependency audit succeeded and Bandit completed with existing B108 `nosec` notices. The run includes the new storage tests. No live S3 provider or Hatchable media was contacted.


### PR #62 CI completion — 2026-11-20

The exact-head workflow Run #906 has completed SUCCESS, all five jobs. Logs confirm 375 passed / 17 skipped / 73 warnings for each Python 3.11, 3.12 and 3.13 matrix job, plus 17 PostgreSQL integration tests passed with 3 warnings. The dependency-audit job succeeded; Bandit emitted existing B108 `nosec` notices. PR #62 remains Draft/open/unmerged, mergeable=true, with zero reviews and zero inline threads. The successful CI verifies test-double/local adapter behavior only; it does not verify a real S3-compatible provider, authorization integration, media transfer, or backup/restore.

## 26. Verified engineering continuation — 2026-11-20 (late run)

### Canonical repository, migration and PR state

- Canonical repository remains `hijaz7861/global-ai-platform`; current default branch `master` is `5add4c1cb6c54b696620d3f3d28ca8708ca6a4cd`. The Alembic head on master is still `0034_postgres_approval_nonces`; role migration `0035_role_entitlements` is not merged.
- Current GitHub inventory: 20 open PRs and 66 branches. GitHub branch metadata reports `master.protected=false`; required branch protection is therefore not established.
- PR #22 remains Draft/open/unmerged. Before this documentation-only update its exact head was `067025940bb1e1dfbcd0574ea94244df4813ece5`, 29 commits ahead / 21 behind master with merge base `21480c93f105052ad35f343b84a6f7858de5631d`. Run #909 passed on that pre-update head. Review APIs show zero submitted reviews and zero inline threads. The branch remains diverged; do not treat mergeable=true as owner approval or integration readiness.
- Original PR #23 remains Draft/open/unmerged at `63b037cfc13ad2d121ca556934f6d2dbe44e6011`, based on the old `21480c9` snapshot and reported non-mergeable. It is retained unchanged to preserve its history.
- New Draft PR #63 is the current role integration candidate. Branch `integration/role-entitlements-master-refresh-20261120`, exact head `4899ca85a3662a0283847f39723a708b0e449b07`, first parent current master, second parent original PR #23 head. GitHub compare reports 0 commits behind and a 14-file content diff. It adds migration `0035_role_entitlements`, role lifecycle and admin review queue, supplier role gates, expiry maintenance, and regression coverage.
- Exact-head GitHub Actions Run #912 for PR #63 / `4899ca85a3662a0283847f39723a708b0e449b07` completed SUCCESS across all five jobs. Python 3.11/3.12/3.13 lint, compile, SQLite migration and test jobs passed; `pip-audit` and Bandit passed; PostgreSQL migration upgrade and `alembic check` passed; PostgreSQL integration reported 20 passed / 3 warnings. Run #910 on the earlier integration head failed because its existing concurrent procurement fixture did not verify supplier email or approve the new supplier role; the fixture was corrected and Run #912 passed. PR #63 remains Draft, with zero owner reviews and zero inline threads.
- PR #61 exact-head Run #905 and PR #62 exact-head Run #906 previously passed 5/5 jobs; both remain Draft and unreviewed. PR #20 exact-head Run #611 passed, but authenticated Streamable HTTP POST `initialize`, `tools/list`, and `tools/call` remain unverified. PRs #24–#28 have historical successful runs #759, #761, #764, #766 and #768 respectively, but their role/vertical bases are stale and must be reconciled after PR #63. None of these PRs is owner-approved.

### Role-entitlement implementation verified in PR #63

- Role definitions and independent global/organization-scoped entitlement records are modeled with partial unique indexes for pending/active/suspended lifecycle states.
- Application and approval require verified email. Paid roles cannot become active solely from a user-supplied payment reference; a verified platform administrator must explicitly confirm payment. Professional roles retain an 18+ floor through fee configuration, approval, reinstatement and runtime authorization.
- Suspend, revoke, reinstate, bounded expiry and append-only role-event controls are present. SQLite and PostgreSQL event immutability are covered. Role expiry is integrated into the current one-shot maintenance runner without dropping the already-merged AI memory-retention purge.
- The administrator queue is bounded and filterable by role/status; it returns an internal applicant ID but not applicant email or identity documents.
- PostgreSQL concurrency tests cover global and organization-scoped uniqueness. Supplier quote submission/revision/withdrawal and purchase-order fulfilment now require an active supplier entitlement in the exact organization scope while preserving tenant membership/ownership checks.
- Remaining role gates must still be audited against every supplier, transport, construction, property, workforce and education endpoint. Payment and age verification are manual admin attestations, not provider integrations. Guardian consent for student minors is separate and absent. A real existing-database upgrade from a `0034` snapshot has not yet been rehearsed; CI verified empty-database migration paths and PostgreSQL integration.

### Read-only Hatchable Version 40 refresh

- The connected project remains `HIJAZ ONE`, slug `hijaz-construction`, Version 40, at https://hijaz-construction.hatchable.site. No file, schema, record, deployment or environment was changed.
- Current inventory: 75 project files, 25 active API functions, 46 PostgreSQL tables, 14 SQL migrations, and one active hourly `/api/jobs/follow-up` task. The scheduler reports 168 runs and 0 errors in the last 7 days; latest recorded run was 2026-11-20T18:05:19 UTC.
- The admin-only `/api/backup` remains incomplete for migration: it exports 17 of 46 table datasets plus media metadata/keys, not object bytes, and there is no import endpoint. No backup was executed and no row data or media bytes were read or copied.
- This inventory confirms the current source application remains live and separate from the portable repository. No schema mapping or data transfer is claimed.

### Definition of Done and release boundary

The HIJAZ ONE Definition of Done is still **not satisfied**. In particular: all verticals are not yet ported; Hatchable backup/import and full 46-table/object-byte reconciliation are incomplete; no staged import/restore or independent-host rehearsal has passed; portable scheduler/worker and object-storage adapters are not fully wired to all business routes; global commerce/payment settlement is incomplete; AI approval/checkpoint/resumption, complete DLP/evaluation and retention/deletion controls remain incomplete; MCP authenticated POST verification remains outstanding; owner review and required branch protection are absent.

No PR was merged. No production deployment, live Hatchable mutation, export/import, record copy, secret change, provider activation, paid service, domain purchase or repository rename occurred.

### Next safe engineering sequence

1. Obtain owner review of Draft PR #63; keep it unmerged until reviewed. Then refresh PRs #24–#28 in dependency order, using PR #63 as their role-foundation base and checking each endpoint's exact organization/tenant scope.
2. Complete a full role-protected endpoint matrix, including negative tests for pending, expired, suspended, revoked, wrong-organization, unverified-email and inactive-role cases.
3. Design and test a staged export/import package covering all 46 Hatchable tables and actual object bytes with checksums, idempotent import, referential reconciliation, rollback and dry-run comparison. Do not run it against the live project until separately approved.
4. Obtain an authorized authenticated POST-capable path for MCP protocol verification.
5. Continue portable runtime, commerce/payment reconciliation, AI checkpoints/approval/DLP and release-rehearsal work. Do not rename the repository or treat the current product as production-ready.

This snapshot records verified source and CI evidence only; it is not a release approval or a Definition-of-Done completion claim.

## 27. Verified engineering continuation — 2026-11-22 (workforce directory)

### Current repository and migration state

- Canonical repository: hijaz7861/global-ai-platform. Default branch master is 11ad7da84e73126a450cb0d194e4859b90263a4a; master Alembic head is 0034_postgres_approval_nonces. The API returned 77 branches, none protected, and 29 open Draft/open PRs. Review-thread and submitted-review checks returned zero entries across the inspected open PR set.
- PR #22 remains Draft/open/unmerged. Its prior head d277cc26cd3be07ed3914acad035ba898449f850 passed Run #942 (5/5). The documentation continuation advanced its head to f27588d00873d60d403bac3c2f1cafabab10f1c2, 45 commits ahead / 23 behind current master; exact-head Run #944 passed 5/5. This workforce documentation update creates another documentation-only head and requires a fresh exact-head run; Run #944 must not be treated as proof for the new commit.
- The consolidated vertical stack is #69 → #70 → #71 → #72. PR #69 is Draft on current master, exact-head Run #929 passed 5/5 (Run #928 was cancelled). PR #70 is stacked on #69, exact-head Run #938 passed 5/5. PR #71 is stacked on #70, exact-head Run #940 passed 5/5. All remain unmerged and unreviewed.
- PR #72, feat/hijaz-one-workforce-directory-20261122, is Draft on #71 at base 55c8d41ef60b9fcf48ef814bc119eda702d91c06; exact head is 473eeededd0f98cd9dedfeeb14749fab340adf43. Exact-head Run #946 passed 5/5: Python 3.11/3.12/3.13 each reported 432 passed / 25 skipped; PostgreSQL integration reported 25 passed. Ruff, compile, SQLite migration upgrade/alembic check, PostgreSQL migration apply/schema verification, dependency audit and Bandit passed. The only Bandit output was existing B108 nosec notices in sandbox_runner.py; no new issues were identified. The candidate diff is eight files.
- Candidate migration chain now reaches 0042_workforce_directory. Revisions 0035–0042 are stacked Draft candidates only; none is on master or applied to production.
- The earlier branch feat/hijaz-one-workforce-directory still points to the old property-listings head b97648dadfecb3761c4ac300c1cc0da3205ae433; it is not a workforce implementation. PR #72 is the actual workforce candidate.
- PR #23 remains Draft/open/unmerged at 63b037cfc13ad2d121ca556934f6d2dbe44e6011 with no exact-head workflow run. Its role foundation is consolidated into #69; do not merge it independently.
- PR #20 remains Draft/open at 2922fe4c1bbd2117041f92486ed0b9461250735b; exact-head Run #931 passed 5/5. Its older Vercel preview/health evidence is from a different commit. Authenticated Streamable HTTP POST initialize, tools/list and tools/call remain unverified; keep Vercel SSO protection enabled until an authorized POST-capable path is used.
- PR #68 exact-head Run #930 passed, but its 0035_ai_agent_checkpoints migration conflicts with 0035_role_entitlements in the consolidated stack. It must be renumbered/rebased and re-tested before integration.
- PR #61 (scheduler) and #62 (S3-compatible object storage) have exact-head Runs #905 and #906 successful, but remain Draft/unreviewed. PRs #63–#67 and #24–#28 are earlier standalone/refresh candidates for work consolidated in #69 and remain unmerged. PRs #43–#50 are separate governed-agent candidates with successful recorded exact-head runs, but remain Draft/unreviewed. Exact-head CI for maintenance/dependency candidates #2, #3, #4 and #10 was also checked. No owner review or inline review thread was present in the inspected open PR set.

### Workforce and hiring source inventory

- Read-only inspection of active Hatchable Version 40 confirmed workforce_profiles, workforce_registration_fee_settings and service_requests, plus API functions api/marketplace-action.js, api/marketplace.js, api/marketplace-admin.js and api/provider-workspace.js.
- The source workforce_profiles table has a unique user_id and one free-text trade. Portable PR #72 deliberately uses a unique user_id + role_key pair so a user can hold contractor, mason and labour entitlements independently. Any future import must map the one source profile/trade to the correct role without duplicating or silently changing it; no import has occurred.
- The live fee settings query returned active contractor PKR 500, mason PKR 200, labour PKR 50 and other PKR 0. The portable role catalogue already contains contractor/mason/labour fees as 50,000/20,000/5,000 PKR minor units. The ambiguous other group is not exposed as a professional role.
- PR #72 adds one portable profile per user and role for contractor/mason/labour, exact-role require_active_role write gates, public discovery/detail filtering against current global entitlement, active account/email, age review, fee/payment state and expiry, and administrator content moderation. Profile edits reset moderation to pending. Public responses omit user IDs, email, phone, GPS coordinates, identity/age references, payment references and reviewer notes. Age/fee proof is not duplicated in the profile; no GPS-based road ETA is claimed.
- The source service_requests schema includes client/provider IDs, service type, title/details, location, numeric budget, status, verified-phone snapshot, client acceptance, provider terms acknowledgement, payment method and a PKR 50 platform-fee field/status. Source marketplace-action requires verified phone plus both acknowledgement booleans; provider-workspace only returns assigned requests after the platform fee is paid and the provider identity status is verified, then allows bounded job-state transitions. This is source evidence, not proof of a portable implementation.
- Next workforce slice: port service_requests with decimal-safe PKR minor units, trusted UserPhoneVerification rather than user-supplied phone proof, role-gated published provider assignment, explicit versioned client/provider terms acceptance, manual evidence-reviewed fee payment, idempotent status transitions and strict phone/contact visibility after authorized release. Do not enable JazzCash or another external provider. No hiring request has been imported or implemented in PR #72.

### Definition of Done status and next work

**NOT SATISFIED.** PR #72's workforce directory is implemented and exact-head CI passed, but it remains Draft/unreviewed and stacked on unmerged parents. Hiring/service requests, contractor-company membership, workforce ratings/reviews, phone verification integration for hiring, remaining source verticals, portable worker/scheduler/object-storage integration, global commerce/FX/tax, signed/idempotent payment webhooks/refunds/settlement, complete AI controls, authenticated MCP POST verification, full staged Hatchable export/import with object-byte/financial/consent/audit reconciliation, backup/restore, independent-host rehearsal, security review, branch protection and owner approval remain open gates.

No PR was merged, no production deployment or live Hatchable write occurred, no secret/provider was enabled, no data was copied/deleted, and no domain or paid service was purchased. Keep the repository name and live application unchanged.
