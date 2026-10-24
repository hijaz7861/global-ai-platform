# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, OpenAPI, root service identity, and versioned health endpoint.
- Registration with normalized email, Argon2 password hashing, single-use email ownership verification tokens, registration throttling, signed bearer tokens, server-side logout revocation, and fail-closed signing-key validation.
- Password recovery with random one-time tokens, SHA-256 digests at rest, expiry, atomic single-use consumption, generic request responses, configurable SMTP delivery, and invalidation of existing sessions. Login, registration, password-reset, and email-verification requests are rate-limited using persistent database-backed sliding windows with per-subject atomic upsert locks on SQLite/PostgreSQL. Login requires verified email by default (`REQUIRE_EMAIL_VERIFICATION=true`); verification tokens are single-use, expire, and are stored as SHA-256 digests.
- Organization creation and membership/role controls with tenant-scoped access and audit events.
- Tenant-owned catalog listings, draft/active publication, bounded public search with currency/price filters and pagination, conditional stock reservation, idempotent orders, buyer-scoped reads, cancellation and seller-scoped summaries. Owner/admin inventory adjustments now create append-only before/after history rows in the same transaction as audit events. Orders support legacy single-line payloads and multi-item carts (up to 50 unique listings), reserve each line atomically, enforce one currency and bounded totals, and have bounded unpaid expiry timestamps with a platform-admin-only idempotent sweep that restores stock and records an audit event.
- Supplier profile onboarding per organization, draft/submitted/approved/rejected workflow, rejection reason validation, resubmission after rejection, and platform-admin review guarded by a configured email allowlist. Initial procurement requests and supplier quotes now support approved-supplier submissions, tenant-scoped quote comparison, single-winner award, request closure, and audit events.
- Tenant-scoped chart of accounts, balanced idempotent double-entry journal posting, immutable one-time reversal entries, organization-scoped accounting period creation/listing/closure, guarded posting after period setup, period-specific derived trial balance, and all-time derived trial balance using integer minor units.
- Transaction-bound allowlisted audit events for implemented sensitive operations.
- Platform-admin security maintenance endpoint cleans expired verification/reset tokens, expired access revocations, fully expired refresh-token families, and stale auth-throttle rows.
- Guarded OpenAI-compatible adapter with bounded output/timeouts, safe errors, no automatic retries, rolling request quota, PostgreSQL locking requirement, and live calls disabled by default.
- Alembic migrations through persistent auth throttling, email verification, inventory history, and refresh token rotation.
- GitHub Actions CI for Ruff, compile checks, Alembic migration consistency, and pytest across Python 3.11–3.13, plus a PostgreSQL 16 service job for full migrations, schema consistency, and concurrent refresh-token replay integration.
- Optional Docker packaging that applies migrations before starting the API.

## Verification status
CI run 175 passed lint, compilation, Alembic migration consistency, and 54 tests on Python 3.11, 3.12, and 3.13 after refresh-token rotation was added. CI run 197 passed lint, compilation, Alembic migration consistency, and 58 tests on Python 3.11, 3.12, and 3.13 for order expiry and multi-item cart behavior. CI run 214 passed lint, compilation, Alembic migration consistency, and 60 tests on Python 3.11, 3.12, and 3.13 for supplier procurement. The prior 54 SQLAlchemy foreign-key-cycle warnings were resolved by marking the migration-managed cycle explicitly; only the existing Starlette/httpx deprecation warning remains. One Starlette/httpx deprecation warning remains. No production deployment, live payment processing, or live paid AI request has occurred.

## Important remaining gaps
- proxy-aware IP handling. A PostgreSQL 16 CI service now verifies migrations and concurrent refresh-token replay behavior; broader PostgreSQL workload/concurrency coverage remains open. Signing-key rotation now supports configured previous keys for verification; operators must remove previous keys after the old access-token lifetime and planned rotation window. Persistent account lockout is configured by `AUTH_ACCOUNT_LOCKOUT_THRESHOLD` and `AUTH_ACCOUNT_LOCKOUT_MINUTES`; failed-password counts clear after a successful login once the lock has expired. Refresh tokens are hashed, rotated, replay-protected, and cleaned only after every token in a family expires. Registration/login/password-reset/email-verification endpoints have database-backed request limits; proxy-aware client IP handling still needs deployment-specific review.
- Membership invitations and broader role administration.
- Audit tamper-evidence, retention controls, and external log export.
- Period-specific reporting and accounting-date persistence; database-enforced non-overlap/concurrency handling; full financial statements, reconciliation, payments, refunds, tax, and seller settlement.
- Shipping, tax, product variants, external scheduler provisioning, and concurrency testing of all inventory/order paths.
- Multi-provider AI routing/fallback, provider-specific native APIs, monetary budgets, tool calls, and autonomous agent workflows.
- Purchase-order generation, quote revisions/withdrawals, procurement approval thresholds, supplier fulfilment, logistics, rentals, real estate, workforce, education, OAuth connectors, and webhooks.
- PostgreSQL concurrency/service integration tests, independent security review, monitoring, backup/restore exercises, and staged deployment.

## Next engineering gates
1. Add MFA, account lockout, expired-token cleanup, and concurrency-safe shared rate limiting.
2. Extend accounting with period-specific reporting, reconciliation, and concurrency-safe period enforcement.
3. Add supplier procurement and quote workflows.
4. Expand provider routing and bounded agent orchestration.
5. Add connector OAuth/webhook lifecycle.
6. Add integration tests and operational readiness gates.

CI passing is not equivalent to production readiness. Production deployment and paid services require explicit owner approval.
