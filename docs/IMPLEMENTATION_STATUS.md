# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, OpenAPI, root service identity, and versioned health endpoint.
- Registration with normalized email, Argon2 password hashing, single-use email ownership verification tokens, registration throttling, signed bearer tokens, server-side logout revocation, and fail-closed signing-key validation.
- Password recovery with random one-time tokens, SHA-256 digests at rest, expiry, atomic single-use consumption, generic request responses, configurable SMTP delivery, and invalidation of existing sessions. Login, registration, password-reset, and email-verification requests are rate-limited using persistent database-backed sliding windows with per-subject atomic upsert locks on SQLite/PostgreSQL. Login requires verified email by default (`REQUIRE_EMAIL_VERIFICATION=true`); verification tokens are single-use, expire, and are stored as SHA-256 digests.
- Organization creation and membership/role controls with tenant-scoped access and audit events.
- Tenant-owned catalog listings, draft/active publication, bounded public search with currency/price filters and pagination, conditional stock reservation, idempotent orders, buyer-scoped reads, cancellation and seller-scoped summaries. Owner/admin inventory adjustments now create append-only before/after history rows in the same transaction as audit events. Orders support legacy single-line payloads and multi-item carts (up to 50 unique listings), reserve each line atomically, enforce one currency and bounded totals, and have bounded unpaid expiry timestamps with a platform-admin-only idempotent sweep that restores stock and records an audit event.
- Supplier profile onboarding per organization, draft/submitted/approved/rejected workflow, rejection reason validation, resubmission after rejection, and platform-admin review guarded by a configured email allowlist. Initial procurement requests and supplier quotes now support approved-supplier submissions, tenant-scoped quote comparison, single-winner award, request closure, audit events, and a single purchase order generated from the awarded quote with idempotent issue, buyer cancellation, and supplier fulfilment.
- Tenant-scoped chart of accounts, balanced idempotent double-entry journal posting, immutable one-time reversal entries, organization-scoped accounting period creation/listing/closure, database-enforced non-overlap triggers, guarded posting after period setup, period-specific derived trial balance, all-time derived trial balance, bounded cursor-paginated account ledger with opening/running balances, a date-filtered income statement, and a derived balance sheet grouped by currency using integer minor units.
- Transaction-bound allowlisted audit events for implemented sensitive operations. Migration 0024 adds database triggers that reject UPDATE/DELETE mutations to audit rows on PostgreSQL and SQLite.
- Platform-admin security maintenance endpoint cleans expired verification/reset tokens, old accepted/revoked/expired invitations, expired access revocations, fully expired refresh-token families, and stale auth-throttle rows. A bounded one-shot runner (python -m app.maintenance) runs the security cleanup and unpaid-order expiry together for external cron/CronJob scheduling.
- Guarded OpenAI-compatible adapter with bounded output/timeouts, safe errors, no automatic retries, rolling request quota, PostgreSQL locking requirement, and live calls disabled by default.
- Alembic migrations through persistent auth throttling, email verification, inventory history, refresh token rotation, membership invitations, account lockout, purchase orders, and supplier quote revision history.
- GitHub Actions CI for Ruff, compile checks, Alembic migration consistency, and pytest across Python 3.11–3.13, plus a PostgreSQL 16 service job for full migrations, schema consistency, and concurrent refresh-token replay integration.
- Optional Docker packaging that applies migrations before starting the API.

## Verification status
CI run 416 at commit `d3227207307837e162b21e0069ae4740c78894d6` passed Ruff, compilation, Alembic migration consistency, and 81 passed / 6 skipped tests on each of Python 3.11, 3.12, and 3.13. The PostgreSQL 16 integration job passed all 6 tests, including concurrent order cancellation without double stock release. A new cancellation-versus-expiry race test has been added and is pending CI verification. The dependency audit reported no known vulnerabilities, and Bandit reported no issues at its configured low-confidence/medium-severity threshold. These are automated checks, not a comprehensive independent security audit. No production deployment, live payment processing, or live paid AI request has occurred.

## Important remaining gaps
- Broader PostgreSQL workload/concurrency coverage remains open. The concurrent order-cancellation and cancellation-versus-expiry regression tests passed in PostgreSQL CI run 429; broader race-condition coverage remains open. Authentication throttling now resolves X-Forwarded-For only when the direct peer matches an explicit TRUSTED_PROXY_IPS entry; deployment operators must configure exact proxy IPs/CIDRs or leave it empty. CI includes spoofing, trusted-chain, and malformed-header tests.
- Signing-key rotation requires an operator-controlled procedure: configure `PREVIOUS_SECRET_KEYS`, run `python -m app.rotate_mfa_secrets` to re-encrypt stored MFA secrets, verify MFA login, retain old keys until prior access tokens expire, then remove them. This procedure still requires staging validation.
- Broader role administration and organization membership-role changes.
- Cryptographic audit hash chaining, retention policy, and external log export. Database-level UPDATE/DELETE protection is implemented; it does not by itself prevent a privileged database operator from disabling triggers.
- Cash-flow statements, payment reconciliation, payments, refunds, tax, seller settlement, and closing workflows remain open. The new manual bank-statement reconciliation MVP is not a bank connector or payment reconciliation. The balance sheet includes accumulated net income as current earnings until a closing workflow is implemented.
- External scheduler provisioning and operational alerting remain operator-owned; the one-shot runner is provided. Full inventory/order race-condition coverage remains open.
- Multi-provider AI routing/fallback, provider-specific native APIs, monetary budgets, tool permissions, and autonomous agent workflows.
- Procurement approval thresholds, logistics, rentals, real estate, workforce, education, OAuth connectors, and webhooks.
- Independent security review, monitoring/alerting, backup/restore exercises, and staged deployment.

## Next engineering gates
1. Complete accounting reports, reconciliation, and concurrency-safe period enforcement.
2. Add bounded multi-provider AI routing, budget controls, and tool permissions.
3. Add OAuth/webhook lifecycle and external scheduler provisioning.
4. Expand PostgreSQL concurrency tests for inventory, orders, accounting periods, and procurement.
5. Complete threat-model review, monitoring, backup/restore drills, and staged deployment readiness.

CI passing is not equivalent to production readiness. Production deployment and paid services require explicit owner approval.
