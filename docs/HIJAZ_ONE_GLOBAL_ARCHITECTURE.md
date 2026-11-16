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
