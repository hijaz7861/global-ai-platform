# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, OpenAPI, root service identity, and versioned health endpoint.
- Registration with normalized email, Argon2 password hashing, single-use email ownership verification tokens, registration throttling, signed bearer tokens, server-side logout revocation, and fail-closed signing-key validation.
- Password recovery with random one-time tokens, SHA-256 digests at rest, expiry, atomic single-use consumption, generic request responses, configurable SMTP delivery, and invalidation of existing sessions. Login, registration, password-reset, and email-verification requests are rate-limited using persistent database-backed sliding windows with per-subject atomic upsert locks on SQLite/PostgreSQL. Login requires verified email by default (`REQUIRE_EMAIL_VERIFICATION=true`); verification tokens are single-use, expire, and are stored as SHA-256 digests.
- Organization creation and membership/role controls with tenant-scoped access and audit events.
- Tenant-owned catalog listings, draft/active publication, bounded public search with currency/price filters and pagination, conditional stock reservation, idempotent orders, buyer-scoped reads, cancellation and seller-scoped summaries. Owner/admin inventory adjustments now create append-only before/after history rows in the same transaction as audit events. Orders support legacy single-line payloads and multi-item carts (up to 50 unique listings), reserve each line atomically, enforce one currency and bounded totals, and have bounded unpaid expiry timestamps with a platform-admin-only idempotent sweep that restores stock and records an audit event.
- Supplier profile onboarding per organization, draft/submitted/approved/rejected workflow, rejection reason validation, resubmission after rejection, and platform-admin review guarded by a configured email allowlist. Initial procurement requests and supplier quotes now support approved-supplier submissions, tenant-scoped quote comparison, single-winner award, request closure, and audit events.
- Tenant-scoped chart of accounts, balanced idempotent double-entry journal posting, immutable one-time reversal entries, organization-scoped accounting period creation/listing/closure, guarded posting after period setup, period-specific derived trial balance, all-time derived trial balance, and a bounded cursor-paginated account ledger with opening/running balances using integer minor units.
- Transaction-bound allowlisted audit events for implemented sensitive operations.
- Platform-admin security maintenance endpoint cleans expired verification/reset tokens, old accepted/revoked/expired invitations, expired access revocations, fully expired refresh-token families, and stale auth-throttle rows.
- Guarded OpenAI-compatible adapter with bounded output/timeouts, safe errors, no automatic retries, rolling request quota, PostgreSQL locking requirement, and live calls disabled by default.
- Alembic migrations through persistent auth throttling, email verification, inventory history, and refresh token rotation.
- GitHub Actions CI for Ruff, compile checks, Alembic migration consistency, and pytest across Python 3.11–3.13, plus a PostgreSQL 16 service job for full migrations, schema consistency, and concurrent refresh-token replay integration.
- Optional Docker packaging that applies migrations before starting the API.

## Verification status
CI run 313 passed lint, compilation, Alembic migration consistency, and 69 tests (1 PostgreSQL-only test skipped) on Python 3.11, 3.12, and 3.13. Its PostgreSQL 16 job passed lint, all migrations, schema consistency, and 1 concurrent refresh-token replay integration test. The membership invitation lifecycle, cleanup, account lockout, and account-ledger pagination are included in this run. The prior 54 SQLAlchemy foreign-key-cycle warnings were resolved by marking the migration-managed cycle explicitly; only the existing Starlette/httpx deprecation warning remains. One Starlette/httpx deprecation warning remains. No production deployment, live payment processing, or live paid AI request has occurred.

## Important remaining gaps
- Deployment-specific proxy-aware client IP handling and broader PostgreSQL workload/concurrency coverage. CI now runs migrations and a concurrent refresh-token replay test against PostgreSQL 16.
- Safe key rotation still requires an operator-controlled procedure: configure the new `SECRET_KEY`, retain prior keys only for the old access-token lifetime, then remove them.
- Broader role administration and organization membership-role changes.
- Audit tamper-evidence, retention policy, and external log export.
- Full financial statements, reconciliation workflows, payments, refunds, tax, seller settlement, and database-enforced accounting-period overlap/concurrency controls.
- External scheduler provisioning and full inventory/order race-condition coverage.
- Multi-provider AI routing/fallback, provider-specific native APIs, monetary budgets, tool permissions, and autonomous agent workflows.
- Purchase-order generation, quote revisions/withdrawals, procurement approval thresholds, supplier fulfilment, logistics, rentals, real estate, workforce, education, OAuth connectors, and webhooks.
- Independent security review, monitoring/alerting, backup/restore exercises, and staged deployment.

## Next engineering gates
1. Complete accounting reports, reconciliation, and concurrency-safe period enforcement.
2. Add bounded multi-provider AI routing, budget controls, and tool permissions.
3. Add OAuth/webhook lifecycle and external scheduler provisioning.
4. Expand PostgreSQL concurrency tests for inventory, orders, accounting periods, and procurement.
5. Complete threat-model review, monitoring, backup/restore drills, and staged deployment readiness.

CI passing is not equivalent to production readiness. Production deployment and paid services require explicit owner approval.
