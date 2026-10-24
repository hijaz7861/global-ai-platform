# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, OpenAPI, root service identity, and versioned health endpoint.
- Registration with normalized email, Argon2 password hashing, signed bearer tokens, server-side logout revocation, and fail-closed signing-key validation.
- Password recovery with random one-time tokens, SHA-256 digests at rest, expiry, atomic single-use consumption, generic request responses, configurable SMTP delivery, and invalidation of existing sessions. Login and password-reset requests are rate-limited using persistent database-backed sliding windows.
- Organization creation and membership/role controls with tenant-scoped access and audit events.
- Tenant-owned catalog listings, draft/active publication, bounded public search with currency/price filters and pagination, inventory adjustment, atomic stock reservation, idempotent orders, buyer-scoped reads, cancellation and seller-scoped summaries.
- Supplier profile onboarding per organization, draft/submitted/approved/rejected workflow, rejection reason validation, resubmission after rejection, and platform-admin review guarded by a configured email allowlist.
- Tenant-scoped chart of accounts, balanced idempotent double-entry journal posting, and derived trial balance using integer minor units.
- Transaction-bound allowlisted audit events for implemented sensitive operations.
- Guarded OpenAI-compatible adapter with bounded output/timeouts, safe errors, no automatic retries, rolling request quota, PostgreSQL locking requirement, and live calls disabled by default.
- Alembic migrations through supplier profiles and password-reset tokens.
- GitHub Actions CI for Ruff, compile checks, Alembic migration consistency, and pytest across Python 3.11–3.13.
- Optional Docker packaging that applies migrations before starting the API.

## Verification status
The latest code workflow passed lint, compilation, migration consistency, and tests on Python 3.11, 3.12, and 3.13 for the supplier-onboarding commit. The following documentation/config-example update is being checked separately. No production deployment, live payment processing, or live paid AI request has occurred.

## Important remaining gaps
- Email ownership verification, MFA, refresh-token rotation, registration throttling, account lockout, and expired-token cleanup. Rate limits currently apply to login and password-reset request endpoints.
- Membership invitations and broader role administration.
- Audit tamper-evidence, retention controls, and external log export.
- Journal reversals, period closing, full financial statements, reconciliation, payments, refunds, tax, and seller settlement.
- Multi-line carts, inventory adjustment history, order expiry, shipping, tax, and product variants.
- Multi-provider AI routing/fallback, provider-specific native APIs, monetary budgets, tool calls, and autonomous agent workflows.
- Supplier quotes/procurement/fulfilment, logistics, rentals, real estate, workforce, education, OAuth connectors, and webhooks.
- PostgreSQL concurrency/service integration tests, independent security review, monitoring, backup/restore exercises, and staged deployment.

## Next engineering gates
1. Add shared rate limiting and account-security controls.
2. Add journal reversals and period controls.
3. Add supplier procurement and quote workflows.
4. Expand provider routing and bounded agent orchestration.
5. Add connector OAuth/webhook lifecycle.
6. Add integration tests and operational readiness gates.

CI passing is not equivalent to production readiness. Production deployment and paid services require explicit owner approval.
