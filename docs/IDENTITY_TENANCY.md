# Identity and Tenant Foundation

## Implemented
- Registration normalizes email and requires a minimum 12-character password; passwords are hashed with Argon2.
- Email verification uses expiring single-use tokens stored as SHA-256 digests. Verification is required by default and SMTP delivery is configurable.
- Login issues short-lived signed access tokens and rotating refresh tokens. Only refresh-token digests are stored; replay revokes the session family. Logout revokes the current session family.
- Password reset uses random, expiring, single-use tokens; successful reset increments the user's auth version and invalidates existing sessions. Responses do not disclose whether an account exists.
- TOTP MFA supports encrypted secrets, enrollment confirmation, recovery codes stored as hashes, replay protection, and password reauthentication for enrollment/disable.
- Persistent failed-login lockout and database-backed request throttling are implemented. Forwarded client IPs are trusted only when the direct peer is configured in `TRUSTED_PROXY_IPS`.
- Invitation lifecycle supports owner/admin invite, recipient acceptance using a single-use token, and revocation of pending invitations. SMTP must be configured to deliver invitations.
- Organization creation makes the creator owner. Membership and tenant-owned resources are scoped to organization membership; role-management operations are constrained and audited.
- PostgreSQL CI covers concurrent refresh-token replay, inventory reservation, cancellation without double stock release, accounting-period creation, and audit immutability.

## Operational requirements and limitations
- Set a unique random `SECRET_KEY` of at least 32 characters in a deployment secret manager. Never commit credentials or use test keys.
- To rotate signing keys, configure `PREVIOUS_SECRET_KEYS`, run `python -m app.rotate_mfa_secrets` to re-encrypt stored MFA secrets, verify MFA login and the re-encryption count, keep the old key until prior access tokens expire, and remove it only after staging validation.
- Configure `TRUSTED_PROXY_IPS` with exact proxy IPs/CIDRs only when deployed behind a trusted proxy; leave it empty for direct connections.
- Email verification, password reset and invitations require working SMTP delivery.
- Broader role administration, enterprise SSO/OAuth, full tenant-wide security audit, operational monitoring, backups and incident response remain incomplete.
- Passing CI does not prove production readiness. See [Production Readiness Gate](PRODUCTION_READINESS.md).

## Local setup
1. Copy `.env.example` to `.env`.
2. Set a unique random `SECRET_KEY` of at least 32 characters.
3. Install requirements.
4. Run `python -m alembic upgrade head`.
5. Start the API with Uvicorn.
