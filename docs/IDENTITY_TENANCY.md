# Identity and Tenant Foundation

## Implemented in the foundation branch
- Registration with normalized email and a minimum 12-character password.
- Password hashing through pwdlib's recommended Argon2 configuration.
- Short-lived signed bearer tokens; token issuance is disabled until `SECRET_KEY` is configured.
- User, organization, and membership tables.
- An organization can only be read by a user with a membership record; non-members receive a non-disclosing 404.
- Organization listings are filtered by the authenticated user's membership.
- Database schema managed through Alembic migrations.

## Important limitations
- Email ownership verification, password reset, MFA, refresh-token rotation, rate limiting, account lockout, invitation workflows, and enterprise SSO are not implemented.
- Tenant isolation is demonstrated for organization endpoints only. Every future tenant-owned resource must scope queries to authorized membership.
- Use a long random secret in a secret manager for deployed environments. Never use a test key or commit real secrets.
- Before production, add security review, throttling, operational logging, migration/backup tests, and session revocation strategy.

## Local setup
1. Copy `.env.example` to `.env`.
2. Set `SECRET_KEY` to a unique random value for the environment.
3. Install requirements.
4. Run `alembic upgrade head`.
5. Start the API with Uvicorn.
