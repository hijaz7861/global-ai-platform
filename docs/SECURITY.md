# Security Baseline

## Implemented controls in the draft foundation
- Passwords are hashed using Argon2 via pwdlib; plaintext passwords are never stored.
- Signed bearer-token login is disabled unless `SECRET_KEY` is at least 32 characters.
- Password reset uses random one-time tokens, stores only SHA-256 digests, expires tokens, and consumes them atomically.
- Password-reset requests return a generic response to reduce account enumeration; delivery uses configurable SMTP and does not log reset tokens.
- A successful password reset increments the user's authentication version and invalidates previously issued bearer tokens.
- Organization and listing reads are scoped to authenticated membership.
- Orders are visible only to their buyer.
- Order creation uses conditional inventory updates, per-user idempotency keys, and a transaction.
- Audit records are written in the same transaction as selected organization, listing, and order operations.
- Audit detail values are allowlisted; request bodies, passwords, tokens, and secret values are not copied into event details.
- `.gitignore` excludes local environment files, virtual environments, caches, and build artifacts.
- CI checks compilation, database migrations, and tests on Python 3.11–3.13.

## Current limitations
- The API has no email verification, MFA, refresh-token rotation, rate limiting, account lockout, or invitation flow. Password-reset email delivery requires operator-provided SMTP configuration.
- Audit records are application-level records; immutability against a database administrator, external log export, retention controls, and tamper-evidence are not implemented.
- Tenant isolation is demonstrated only on the organization, catalog, audit, and buyer-order routes. Every future tenant-owned query must enforce its own authorization boundary.
- No payment provider or real payment processing is connected.
- PostgreSQL-specific concurrency, production secrets management, backups, and incident response have not been verified.

## Required before production
- Complete threat modeling and an independent security review.
- Add rate limiting, MFA, refresh-token rotation, and a complete secure session lifecycle.
- Verify tenant scoping for every new endpoint and add cross-tenant regression tests.
- Protect audit retention and export logs to a separate access-controlled destination.
- Keep secrets in a secret manager; never commit real credentials.
- Add dependency/security scanning, backup/restore exercises, and incident procedures.
- Restrict connector scopes and outbound network access; apply timeouts and budgets.
- Require explicit human approval for high-risk financial or irreversible actions.
- Use sandbox payment credentials for integration tests.
- Require explicit owner approval before paid services or production deployment.

Never call a system secure solely because these requirements are documented.
