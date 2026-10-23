# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, root service identity, and versioned process health endpoint.
- Registration with normalized, validated email and Argon2 password hashing.
- Short-lived signed bearer tokens; login fails closed unless a 32-character signing key is configured.
- User, organization, membership, catalog listing, inventory, order, order-item, audit-event, account, journal-entry, and journal-line models.
- Alembic migrations for identity/tenancy, catalog, inventory/orders, audit, and double-entry ledger.
- Organization read/list endpoints scoped to authenticated membership.
- Tenant-scoped listing creation and reads; publication restricted to organization owner/admin.
- Order creation uses conditional inventory updates, per-buyer idempotency, and price/title snapshots.
- Buyer-only order reads; cancellation is restricted to the buyer and pending-payment orders and releases stock once.
- Audit events are written transactionally for organization creation, listing creation/publication, order creation/cancellation, account creation, and journal posting.
- Owner/admin chart of accounts and balanced journal posting with account/tenant/currency checks, integer minor-unit amounts, idempotency, and no update/delete API.
- Owner/admin-only, tenant-scoped audit and journal browsing.
- Orders remain pending payment; no payment provider is integrated and no real payment is taken.
- Integration tests cover identity, tenant boundaries, catalog, stock, idempotency, cancellation, audit records, and ledger invariants.
- GitHub Actions workflow checks Python 3.11–3.13, compilation, Alembic migrations, and pytest.
- Docker packaging applies migrations before starting the API.

## Verification status
The inventory/order/cancellation tests passed on Python 3.11–3.13. The accounting foundation and latest audit-related changes are being checked by the current CI run; wait for the latest workflow result before calling the full branch verified. No production deployment has occurred.

## Important remaining gaps
- Email verification, password reset, MFA, token revocation/refresh, and rate limiting.
- Membership invitations and role administration.
- Audit tamper-evidence, retention controls, and external log export.
- Journal reversals, period closing, balances/reports, reconciliation, payments, refunds, tax, and seller settlement.
- Multi-line carts, inventory adjustment permissions, shipping, tax, and product variants.
- PostgreSQL service integration and operational migration/backup tests.
- AI provider routing, agent tool policies, connector OAuth/webhooks, supplier/logistics workflows.
- Security review, monitoring, backups, and staged deployment.

## Next engineering gates
1. Confirm CI passes on the latest branch commit.
2. Add rate limiting and token lifecycle controls.
3. Add audit hardening and ledger reversals/reporting.
4. Add supplier onboarding and seller order operations.
5. Implement AI orchestration only after scoped permissions and budget controls are ready.
