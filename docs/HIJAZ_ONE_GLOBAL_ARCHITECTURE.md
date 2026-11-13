# HIJAZ ONE — Global Platform Architecture & Integration Plan

**Status:** Architecture baseline / implementation plan  
**Target product:** HIJAZ ONE — Integrated Business & Services Ecosystem  
**Repository lineage:** `hijaz7861/global-ai-platform` (retain Git history; rename repository only after the migration and release gates pass)  
**Working branch:** `integration/hijaz-one-platform`

## 1. Product decision

HIJAZ ONE is the product identity. The existing Global AI Platform is the technical foundation and must become the product's core, not a parallel product. Preserve the existing repository, commit history, migrations, security controls, and test evidence. Do not delete or recreate the repository.

The first integration release is a **modular monolith** with independently deployable background workers. Do not split into microservices until measured scale or independent team ownership justifies it.

## 2. Non-negotiable engineering principles

1. **Portable by design:** business logic must not depend on Hatchable, Vercel, or any one cloud vendor.
2. **PostgreSQL as production source of truth:** use standard PostgreSQL, Alembic migrations, documented backup/restore, and tested export/import.
3. **Container-first runtime:** API, worker, scheduler/maintenance runner, and optional MCP bridge run as separate processes/images from the same versioned codebase.
4. **Provider adapters:** email, SMS/WhatsApp, payment, object storage, maps, AI models, and observability are interfaces with replaceable adapters.
5. **Tenant isolation:** every organization-owned record has explicit tenant scope; authorization is enforced server-side, not by UI or URL.
6. **One identity, multiple entitlements:** one user may hold multiple independently verified and independently paid roles. A role's approval or fee never grants another role.
7. **Fail closed:** pending, rejected, expired, suspended, or unpaid entitlements cannot access protected role capabilities.
8. **Auditability:** sensitive role, payment, approval, financial, AI-tool, and data-export operations produce immutable, tenant-scoped audit events.
9. **No fake integrations:** a provider is not called active until credentials, webhooks, failure handling, and end-to-end tests are complete.
10. **Backward-safe migration:** preserve existing Global AI Platform data and HIJAZ ONE Hatchable data until export, mapping, import, and reconciliation have been proven.

## 3. Reusable foundation already present in Global AI Platform

The current `master` README and migration history document these implemented foundations:

- FastAPI, SQLAlchemy, Alembic, Docker packaging, SQLite local development, PostgreSQL deployment support.
- Identity: Argon2 password hashing, email verification, signed access tokens, rotating hashed refresh tokens, replay detection, session-family revocation, logout revocation, password reset, optional encrypted TOTP MFA, account lockout and auth throttling.
- Organizations, memberships, owner/admin access, tenant-scoped authorization.
- Catalog listings, publication controls, search, currency/price filters, bounded pagination.
- Supplier onboarding and platform-admin approval.
- Procurement requests, supplier quotes, quote awards, purchase orders, fulfilment.
- Inventory adjustments, stock reservation, idempotent orders, cancellation and expiry handling.
- Double-entry accounting foundation: chart of accounts, balanced journal posting, reversals, periods, trial balance, ledger, income statement, balance sheet, manual bank reconciliation.
- Transaction-bound audit events, privacy export, maintenance/security cleanup.
- Guarded AI provider adapter with explicit provider selection and bounded request limits.
- Docker Compose baseline with PostgreSQL.

These capabilities should be extended, not duplicated. Verify the actual code and current tests before relying on any README statement.

## 4. Existing advanced work that is not yet in master

### PR #21 — AI Agent Operating Layer (draft, not merged)

The PR description reports durable tenant-scoped AI tasks, lease-based worker claims/recovery, cancellation, bounded single-agent execution, provenance-aware memory with digest integrity checks, organization-owned versioned skills, static read-only tool gateway, model routing, task-event outbox, and PostgreSQL approval nonce storage.

The PR explicitly reports these remaining gaps: external worker supervisor/health service; trusted human approval issuer/audit service and HTTP release API; durable per-turn checkpoints/resumption; multi-agent planning/role orchestration; vector/embedding memory; retention/hard-delete workflow; automatic skill selection; write-capable tools/memory writes; provider token/cost reservation; provider health/circuit breaking/automatic failover; DLP; full evaluation/observability.

**Merge gate:** inspect the complete diff and review threads, run exact-head CI after the latest commit, validate PostgreSQL integration, resolve review findings, then merge. Do not treat PR description as proof that current head is green.

### PR #20 — Read-only hosted MCP repository bridge (draft, not merged)

The PR description reports a protected, read-only Streamable HTTP MCP bridge for repository metadata, file listing, bounded file reads, recent commits, and CI inspection. Its authenticated GET health check is reported successful; MCP POST protocol operations are not yet verified.

**Merge gate:** authenticated POST `initialize`, `tools/list`, and `tools/call`; preserve deployment protection and path-token secrecy; merge only after protocol tests and review.

## 5. HIJAZ ONE business domains to integrate

The existing Hatchable HIJAZ ONE Version 40 product contains the following business domains and workflows. These are the product requirements to port into the portable core; they are not assumed to be fully production-ready.

1. **Construction & project operations:** projects, clients, quotations, BOQ/estimation, cashbook, labour and material registers, audit trail.
2. **Materials & supplier marketplace:** supplier onboarding, product listings, approval, quote/order requests, fee verification.
3. **Transport & machinery marketplace:** transporter profiles, multiple vehicles/equipment, categories, trip/hour/day/km/fixed-job pricing, pickup/drop details, availability, booking, document review.
4. **Property marketplace:** sale/rent listings, listing review, seller-declared legal status, listing fee and sale commission workflow.
5. **Workforce & contractor directory:** skills, profile, age/identity verification, optional GPS, hiring requests.
6. **Education & academy:** student and teacher profiles, guardian consent for minors, teacher age/fee review, academy/courses/materials, course moderation, paid enrolment and access gating.
7. **AI customer engagement:** bilingual assistant, consent-based lead capture, follow-up scheduling, opt-out, membership entitlements, specialist role prompts.
8. **Admin & governance:** approval queues, fee verification, platform settings, audit and operations dashboard.

### HIJAZ ONE rules that must be preserved

- One global user identity; multiple role profiles and entitlements.
- Each paid role has its own configurable registration fee and verification lifecycle.
- Student identity registration may be free; paid course access requires separate course-specific verified payment.
- A teacher/contractor/supplier/transporter approval must not unlock any other role.
- Under-18 users may be students with guardian consent; they cannot activate professional roles restricted to adults.
- Provider, customer, listing, booking, and payment access is always checked server-side.
- Existing manual payment checks must remain explicitly manual until real payment gateways and verified webhooks are implemented.
- Existing straight-line GPS distance must never be represented as road distance or ETA.
- Seller-declared property status must never be represented as title verification or legal clearance.
- No unverified listing or provider becomes public.

## 6. Target modular architecture

```text
HIJAZ ONE
├── apps/
│   ├── api/                 FastAPI application
│   ├── web/                 Responsive web client (separate deployable)
│   ├── worker/              Durable task and outbox workers
│   └── mcp/                 Optional protected MCP bridge
├── app/
│   ├── identity/            Authentication, sessions, MFA
│   ├── tenancy/             Organizations, membership, isolation
│   ├── entitlements/         Multi-role registry, fees, approval, limits
│   ├── marketplace/          Catalog, listings, search
│   ├── suppliers/            Supplier onboarding and procurement
│   ├── orders/               Orders, inventory, fulfilment
│   ├── construction/         Projects, BOQ, quotations
│   ├── transport/            Vehicles, equipment, bookings
│   ├── property/             Sale/rent listings and commissions
│   ├── workforce/            Skills, contractors, hiring
│   ├── education/            Students, teachers, courses, enrolments
│   ├── accounting/           Ledger, invoices, reconciliation
│   ├── payments/             Provider-neutral payments and webhooks
│   ├── ai/                   Router, tasks, memory, skills, tools
│   ├── notifications/        Email, SMS, WhatsApp adapters
│   ├── files/                S3-compatible object storage adapter
│   ├── audit/                Audit and compliance events
│   └── platform_admin/       Global governance and settings
├── migrations/
├── tests/
├── deploy/
│   ├── docker/
│   ├── compose/
│   └── terraform/
└── docs/
```

The layout is a target. Refactor incrementally around existing module boundaries; do not mass-move working code merely to match a diagram.

## 7. Central role-entitlement model

Implement a normalized role registry and per-user entitlements rather than a single mutable `user.role` field.

Minimum concepts:

- `role_definitions`: stable role key, display metadata, age policy, fee policy, configurable limits, enabled state.
- `user_role_entitlements`: user, role, status, verification status, fee/payment reference, granted/expiry/suspension timestamps, reviewer, audit reference.
- `role_permissions`: explicit capability mapping.
- `role_limit_policies`: per-role quotas and feature limits.
- `role_verification_events`: append-only review history.
- `role_fee_payments`: role-specific payment intent/reference and manual/provider verification.
- `course_enrollments`: separate course-specific entitlement, independent of student role.

Entitlement lifecycle: `not_requested -> pending_payment -> pending_review -> active`, with terminal/exception states `rejected`, `expired`, `suspended`, `revoked`. Free roles may bypass payment only through an explicit policy, not by skipping verification. Avoid trusting client-submitted role names, payment status, prices, or approval flags.

Authorization dependency must check identity, tenant scope, entitlement state, role permission, resource ownership, and any applicable quota for every protected API operation.

## 8. Globalization requirements

- Country and subdivision registry; country-aware address and phone normalization.
- ISO 4217 currency codes and decimal-safe money values; no floating-point money.
- Explicit exchange-rate source, timestamp, base currency, and conversion audit trail.
- Locale-aware dates, numbers, language negotiation, and time zones.
- Country-specific tax configuration and compliance hooks; do not claim legal compliance without country review.
- Provider-neutral payment interface with idempotent webhook ingestion, signature verification, replay protection, refunds, disputes, settlement, and reconciliation.
- Logistics provider interface; road routing/ETA only when a real routing provider is integrated.
- Internationalization catalog with Urdu and English as initial locales, not hard-coded UI strings.
- Data residency and retention policies configurable by jurisdiction.

## 9. Portability contract

Core application code must use interfaces, not vendor SDKs directly.

- Runtime contract: OCI Docker images; non-root user; health/readiness endpoints; graceful shutdown; bounded concurrency.
- Database contract: PostgreSQL 16+; Alembic migrations; migration backup; restore rehearsal; no proprietary database functions without an adapter.
- File contract: S3-compatible object storage interface; metadata stored in PostgreSQL; export manifest and checksum.
- Secrets contract: environment variables locally; secret-manager adapter in production; never commit credentials.
- Background processing contract: database-backed durable queue/outbox; worker process independently scalable; idempotent handlers.
- Email/SMS/payment/AI/maps contract: abstract interfaces and adapter contract tests.
- Deployment contract: Docker Compose for low-cost VPS and container platforms; cloud-specific Terraform isolated under `deploy/terraform`.
- Observability contract: structured logs, request IDs, metrics, traces, redaction, and alert rules without hard dependency on one vendor.
- Backup contract: scheduled PostgreSQL backups, object-storage versioning where available, encrypted offsite copies, periodic restore tests.
- Export contract: documented user/tenant export, full database dump, object manifest, checksum validation, and DNS cutover/rollback checklist.

Hatchable may be used as a temporary UI/hosting environment, but the portable API and database must remain deployable without Hatchable. Never assume Hatchable's database, auth, functions, cron, or file storage are automatically portable; explicitly map and export each.

## 10. Delivery phases and acceptance gates

### Phase 0 — Inventory and safe integration
- Inventory master, all open PRs, branches, migration heads, dependencies, tests, deployment files, and current HIJAZ ONE data schema.
- Record every reusable module and every missing domain.
- Produce a source-to-target API/table mapping.
- No destructive migration and no production merge.

### Phase 1 — Secure foundation and PR integration
- Review PR #20 and #21 diffs and comments.
- Run exact-head Python matrix, PostgreSQL integration, migration checks, dependency audit, Bandit, lint.
- Resolve all review threads and merge in dependency order only after checks pass.
- Add worker health/supervision and MCP protocol integration tests.

### Phase 2 — Portable runtime
- Separate API, worker, scheduler, and web build.
- PostgreSQL Compose, object storage adapter, email adapter, health/readiness, structured config.
- Build and run container smoke tests and migration/restore rehearsal.

### Phase 3 — Identity, tenancy and entitlements
- Implement multi-role entitlement model and server-side authorization.
- Map existing supplier, workforce, teacher, student, transporter, contractor profiles.
- Test cross-tenant and cross-role denial, pending/unpaid/suspended role denial, resource ownership, age policy, and per-role limits.

### Phase 4 — Business domain integration
- Migrate construction, materials, procurement, transport, property, workforce, and education.
- Preserve existing fee and approval rules.
- Add API and data migration tests with reconciliation reports.

### Phase 5 — Payments and global operations
- Provider-neutral payment intents, webhook verification, idempotency, refunds, settlement and reconciliation.
- Multi-currency accounting, tax configuration, localization and notification adapters.
- Integrate only providers for which credentials and legal/business setup are supplied.

### Phase 6 — AI operating layer completion
- Human approval service, checkpoints/resumption, role-based agent orchestration, memory retention, skill selection, cost reservation, provider health and evaluation/observability.
- Keep write-capable tools disabled until policy, authorization, audit, approval and sandbox tests pass.

### Phase 7 — Launch and repository rename
- Staging acceptance, security review, load tests, backup restore, portability rehearsal, data reconciliation, operational runbooks.
- Only after release gates pass, rename GitHub repository to `hijaz-one`, update remote references, docs, badges, automation targets and deployment settings.
- Configure `hijaz1.com` only after the user purchases/controls the domain and selects hosting.

## 11. Definition of done

Do not call the platform complete until all are true:

- All migrations apply from an empty PostgreSQL database and from the supported upgrade path.
- Full unit/API suite and PostgreSQL integration suite pass on supported Python versions.
- No unresolved high/critical security findings.
- Every role and tenant boundary has positive and negative authorization tests.
- Payment state cannot be forged by a client; webhook replay and duplicate events are safe.
- All business modules have API, data ownership, moderation, audit and operational coverage.
- AI agent execution has bounded budgets, approvals, cancellation, resumability and observability.
- Backup and restore plus full data export/import are rehearsed successfully.
- The same release runs in local Docker Compose and at least one independent container host.
- Production secrets, SMTP, payment, AI, storage and monitoring integrations are configured and end-to-end tested.
- No test, deployment, domain, payment gateway, or integration is claimed successful without evidence.

## 12. Current blockers / external inputs

- No owned `hijaz1.com` domain has been confirmed.
- No production hosting budget or paid plan has been authorized.
- No payment provider account/credentials or webhook endpoints have been supplied.
- No production SMTP/SMS/WhatsApp credentials have been supplied.
- Hatchable's current app data export and exact schema mapping still need to be performed before any data migration.
- PR #20 and #21 remain drafts until their exact-head verification and review gates are met.

## 13. Immediate next engineering tasks

1. Review PR #21 changed files, comments and exact-head CI; verify its Alembic head and PostgreSQL tests.
2. Review PR #20 MCP bridge and verify Streamable HTTP POST protocol with authorized deployment access.
3. Inspect the full master tree and all migrations; establish one migration-head policy before merging parallel branches.
4. Inventory HIJAZ ONE Hatchable schema and export data in a non-destructive staging copy.
5. Implement role entitlements in a dedicated branch with tests before migrating business modules.
6. Build the portable deployment baseline and verify clean PostgreSQL boot, API health, worker health, backup and restore.
