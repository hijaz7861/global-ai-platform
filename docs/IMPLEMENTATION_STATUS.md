# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, root service identity, and versioned process health endpoint.
- Registration with normalized, validated email and Argon2 password hashing.
- Short-lived signed bearer tokens; login fails closed unless a 32-character signing key is configured.
- User, organization, membership, and catalog listing models.
- Alembic migrations for identity/tenancy and catalog listings.
- Organization read/list endpoints scoped to the authenticated user's memberships.
- Tenant-scoped listing creation and listing reads; publication restricted to organization owner/admin.
- Public catalog only exposes active listings.
- Money stored as positive integer minor units and a three-letter currency code.
- Integration tests covering identity, tenant boundaries, catalog publication, and invalid inputs.
- GitHub Actions workflow with Python 3.11–3.13, compilation, migration verification, and pytest.
- Docker packaging applies database migrations before starting the API.

## Verification status
A prior CI run passed the then-current identity/migration foundation on Python 3.11, 3.12, and 3.13. Catalog and later security/schema changes require their own latest CI result; do not treat earlier runs as proof of the newest commit. No production deployment has occurred.

## Important remaining gaps
- Email verification, password reset, MFA, token revocation/refresh, and rate limiting.
- Role/membership administration and invitation workflow.
- PostgreSQL service integration and operational migration/backup tests.
- Order lifecycle, inventory reservations, payments, refunds, and reconciliation.
- Accounting ledger, AI provider routing, agent tool policies, connector OAuth/webhooks.
- Security review, monitoring, backups, and staged deployment.

## Next engineering gates
1. Confirm CI passes on the latest branch commit.
2. Add order/cart lifecycle and transactional inventory rules.
3. Add explicit membership administration and role checks.
4. Add audit events and rate limiting.
5. Continue one end-to-end module at a time.
