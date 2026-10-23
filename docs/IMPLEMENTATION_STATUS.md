# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, root service identity, and versioned process health endpoint.
- Registration with normalized, validated email and Argon2 password hashing.
- Short-lived signed bearer tokens; login fails closed unless a 32-character signing key is configured.
- User, organization, membership, catalog listing, inventory, order, order-item, audit-event, account, journal-entry, and journal-line models.
- Alembic migrations for identity/tenancy, catalog, inventory/orders, audit events, and double-entry ledger.
- Tenant-scoped catalog and order flows, stock reservations/cancellation, owner/admin inventory adjustments, seller order summaries.
- Transaction-bound allowlisted audit events for organization, catalog, inventory, order, account, and journal operations.
- Owner/admin chart of accounts, balanced journal posting with account/tenant/currency checks, integer minor-unit amounts, idempotency, derived trial balance, and no update/delete API.
- Guarded OpenAI-compatible provider adapter with no retries, bounded timeouts, secret-safe errors, and live calls disabled by default.
- Live AI endpoint requires owner/admin, explicit opt-in, positive rolling 24-hour request limit, configured credentials/model/URL, and PostgreSQL row locking.
- Integration tests cover identity, tenant boundaries, catalog, stock, idempotency, cancellation, audit records, seller views, ledger invariants, and AI provider behavior.
- GitHub Actions CI checks Ruff, compilation, Alembic migration consistency, and pytest on Python 3.11–3.13.
- Docker packaging applies migrations before starting the API.

## Verification status
CI passed the catalog, order, seller-view, audit, and ledger slices on Python 3.11–3.13. The AI adapter and route tests passed on Python 3.11–3.13 before Ruff was added. The latest workflow must finish successfully to verify linting and the complete current branch. No production deployment has occurred.

## Important remaining gaps
- Email verification, password reset, MFA, token revocation/refresh, and rate limiting.
- Membership invitations and role administration.
- Audit tamper-evidence, retention controls, and external log export.
- Journal reversals, period closing, balances/reports, reconciliation, payments, refunds, tax, and seller settlement.
- Multi-line carts, inventory adjustment history, shipping, tax, and product variants.
- Provider-specific native APIs, multi-provider routing/fallback, pricing-aware monetary budgets, AI tool calls and autonomous agent workflows.
- OAuth connectors, webhooks, supplier/logistics workflows, rentals, real estate, workforce, and education.
- PostgreSQL service integration tests, security review, monitoring, backups, and staged deployment.

## Next engineering gates
1. Confirm CI passes on the latest branch commit.
2. Add token lifecycle controls and rate limiting.
3. Add ledger reversals/reporting and financial approval workflow.
4. Add supplier onboarding and fulfilment.
5. Expand AI routing only after provider-specific integration tests and hard spending controls are ready.
