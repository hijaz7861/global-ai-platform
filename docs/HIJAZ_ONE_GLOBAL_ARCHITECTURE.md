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
- Inventory master, PRs, branches, migrations, dependencies, tests, deployment files and Hatchable schema.
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

- No owned `hijaz1.com` domain confirmed.
- No hosting budget or paid plan authorized.
- No payment provider credentials/webhook setup supplied.
- No production SMTP/SMS/WhatsApp credentials supplied.
- Hatchable data export and schema mapping not yet completed.
- PR #20 MCP bridge remains draft pending authorized POST protocol tests.
- Existing HIJAZ ONE AI calls, OTP and external messaging were previously blocked by missing provider credentials; re-test after migration rather than assuming they work.

## 13. Immediate engineering backlog

1. Verify current master after PR #21 merge and inspect Alembic head consistency.
2. Review PR #20 and run authenticated Streamable HTTP POST protocol tests.
3. Inventory all current source files, migrations, test coverage and provider integrations.
4. Export and map Hatchable database/files into a staging copy; reconcile counts.
5. Implement central role entitlements with full authorization tests.
6. Port HIJAZ ONE business verticals incrementally.
7. Complete portable deployment, worker health, backup/restore and independent-host rehearsal.
8. Rename repository only after the release gates above pass.
