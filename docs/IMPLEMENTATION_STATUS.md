# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, root service identity, and versioned process health endpoint.
- Registration with normalized, validated email and Argon2 password hashing.
- Short-lived signed bearer tokens; login fails closed unless a 32-character signing key is configured.
- User, organization, membership, catalog listing, inventory, order, order-item, and audit-event models.
- Alembic migrations for identity/tenancy, catalog listings, inventory/orders, and audit events.
- Organization read/list endpoints scoped to authenticated membership.
- Tenant-scoped listing creation and reads; publication restricted to organization owner/admin.
- Public catalog only exposes active listings.
- Money stored as positive integer minor units.
- Order creation uses conditional inventory updates, a unique per-user idempotency key, and order-item price/title snapshots.
- Buyer-only order reads; cancellation is restricted to the buyer and pending-payment orders and releases stock once.
- Audit events are written transactionally for organization creation, listing creation/publication, and order creation/cancellation.
- Audit details are allowlisted; owner/admin-only audit browsing is tenant-scoped.
- Orders remain pending payment; no payment provider is integrated and no real payment is taken.
- Integration tests cover identity, tenant boundaries, catalog publication, stock checks, idempotency, cancellation, audit records, and buyer privacy.
- GitHub Actions workflow checks Python 3.11–3.13, compilation, Alembic migrations, and pytest.
- Docker packaging applies migrations before starting the API.

## Verification status
CI passed the inventory/order implementation on Python 3.11, 3.12, and 3.13. Audit trail and audit tests have since been added and need the latest CI result before the current branch is marked verified. No production deployment has occurred.

## Important remaining gaps
- Email verification, password reset, MFA, token revocation/refresh, and rate limiting.
- Membership invitations and role administration.
- Audit tamper-evidence, retention controls, and external log export.
- Payment processing, seller settlement, refunds, and reconciliation.
- Multi-line carts, inventory adjustment permissions, shipping, tax, and product variants.
- PostgreSQL service integration and operational migration/backup tests.
- Accounting ledger, AI provider routing, agent tool policies, connector OAuth/webhooks.
- Security review, monitoring, backups, and staged deployment.

## Next engineering gates
1. Confirm CI passes on the latest branch commit.
2. Add rate limiting and token lifecycle controls.
3. Add seller order views and inventory adjustment permissions.
4. Build the double-entry accounting foundation with immutable journal entries.
5. Continue one end-to-end module at a time.
