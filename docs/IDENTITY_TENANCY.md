# Identity and Tenant Foundation

## Implemented in the foundation branch
- Registration with normalized email and a minimum 12-character password.
- Password hashing through pwdlib's recommended Argon2 configuration.
- Short-lived signed bearer tokens; token issuance and validation fail closed unless `SECRET_KEY` contains at least 32 characters.
- Logout revokes the current token server-side until its expiry; revocation events are audited without storing the token itself.
- Password reset issues random one-time tokens, stores only SHA-256 digests, enforces expiry and single use, and audits successful resets.
- Successful password reset increments an authentication version so existing bearer tokens are rejected. Reset request responses do not reveal whether an email is registered; SMTP delivery is configurable.
- User, organization, and membership tables.
- Organization creation makes the creator the initial owner.
- Organization reads/lists and tenant resources require a membership record; non-members receive a non-disclosing 404.
- Owner/admin can list organization members. Owner can assign admin/member roles; admin can add members only.
- Membership removal is audited; owner memberships cannot be removed through the API.
- Database schema managed through Alembic migrations.

## Important limitations
- Email ownership verification, MFA, refresh-token rotation, account lockout, and enterprise SSO are not implemented. Password-reset delivery requires SMTP configuration.
- Rate limiting, refresh-token rotation, and scheduled cleanup of expired revocation/reset-token rows are not implemented.
- There is no invitation email flow; the target user must already have a registered account.
- Tenant isolation is demonstrated for organization, catalog, order, audit, and accounting endpoints only. Every future tenant-owned resource must scope queries to authorized membership.
- Use a long random secret in a secret manager for deployed environments. Never use a test key or commit real secrets.
- Before production, add security review, throttling, operational logging, migration/backup tests, and session revocation strategy.

## Local setup
1. Copy `.env.example` to `.env`.
2. Set a unique random `SECRET_KEY` of at least 32 characters.
3. Install requirements.
4. Run `alembic upgrade head`.
5. Start the API with Uvicorn.
