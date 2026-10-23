# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, root service identity, and versioned process health endpoint.
- Registration with normalized, validated email and Argon2 password hashing.
- Short-lived signed bearer tokens; login fails closed unless a 32-character signing key is configured.
- User, organization, membership, catalog listing, order, and order-item models.
- Alembic migrations for identity/tenancy, catalog listings, inventory, and orders.
- Organization read/list endpoints scoped to the authenticated user's memberships.
- Tenant-scoped listing creation and reads; publication restricted to organization owner/admin.
- Public catalog only exposes active listings.
- Money stored as positive integer minor units.
- Order creation uses a conditional inventory update, a unique per-user idempotency key, and order-item price/title snapshots.
- Order reads are restricted to the buyer; non-owner access receives a non-disclosing 404.
- Orders remain pending payment; no payment provider is integrated and no real payment is taken.
- Integration tests cover identity, tenant boundaries, catalog publication, stock checks, idempotency, and buyer privacy.
- GitHub Actions workflow checks Python 3.11–3.13, compilation, Alembic migrations, and pytest.
- Docker packaging applies migrations before starting the API.

## Verification status
The previous CI run passed the catalog foundation on Python 3.11–3.13. A new CI run for inventory/order changes is in progress; its final result must be checked before marking the latest changes verified. No production deployment has occurred.

## Important remaining gaps
- Email verification, password reset, MFA, token revocation/refresh, and rate limiting.
- Membership invitations and role administration.
- Payment processing, cancellation/refund workflow, seller settlement, and reconciliation.
- Inventory adjustments, multi-item orders, shipping, tax, and product variants.
- PostgreSQL service integration and operational migration/backup tests.
- Accounting ledger, AI provider routing, agent tool policies, connector OAuth/webhooks.
- Security review, monitoring, backups, and staged deployment.

## Next engineering gates
1. Confirm CI passes on the latest branch commit.
2. Add order cancellation/expiry and safe stock release before payment integration.
3. Add audit events and rate limiting.
4. Add seller order views and inventory adjustment permissions.
5. Continue one end-to-end module at a time.
