# Security Baseline

## Implemented controls in the draft foundation
- Passwords are hashed using Argon2 via pwdlib; plaintext passwords are never stored.
- Signed bearer-token login is disabled unless `SECRET_KEY` is at least 32 characters.
- Short-lived access tokens are paired with hashed, rotating refresh tokens. Reuse of a consumed refresh token revokes its session family; logout revokes the family and its access tokens.
- Optional TOTP MFA requires password reauthentication to enroll/disable, encrypts secrets with AES-GCM using a key derived from `SECRET_KEY`, and prevents reuse of the same TOTP time step.
- Password reset uses random one-time tokens, stores only SHA-256 digests, expires tokens, and consumes them atomically.
- Password-reset and email-verification requests return generic responses to reduce account enumeration; delivery uses configurable SMTP and does not log tokens.
- Registration, login, password-reset, and email-verification request endpoints use persistent database-backed rate limits keyed by a digest of normalized account identifier and direct peer address; responses include `Retry-After` when limited. The current count-and-insert limiter is not fully concurrency-safe, and proxy-aware IP handling requires deployment review.
- A successful password reset increments the user's authentication version and invalidates previously issued bearer tokens.
- Supplier profile reads are organization-membership scoped; changes require owner/admin membership, while review requires an authenticated account in the explicit `PLATFORM_ADMIN_EMAILS` allowlist.
- Organization and listing reads are scoped to authenticated membership.
- Orders are visible only to their buyer; unpaid orders have a configurable expiry and a platform-admin-only idempotent sweep releases reserved stock once.
- Procurement requests and quotes enforce buyer/supplier tenant boundaries, require approved suppliers to quote, and support a single awarded quote.
- Order creation uses conditional inventory updates, per-user idempotency keys, and a transaction.
- Audit records are written in the same transaction as selected organization, listing, and order operations.
- Audit detail values are allowlisted; request bodies, passwords, tokens, and secret values are not copied into event details.
- `.gitignore` excludes local environment files, virtual environments, caches, and build artifacts.
- A platform-admin-only cleanup endpoint removes expired verification/reset tokens, expired access-token revocations, fully expired refresh-token families, and stale rate-limit rows. It retains refresh-token history while any token in that family remains unexpired.
- CI checks compilation, database migrations, and tests on Python 3.11–3.13.

## Current limitations
- MFA has no recovery-code flow yet. MFA secret decryption depends on retaining the same `SECRET_KEY`; rotating that key without a planned re-enrollment strategy makes existing MFA secrets unreadable.
- MFA has no recovery-code flow yet. MFA secret decryption depends on retaining the same `SECRET_KEY`; rotating that key without a planned re-enrollment strategy makes existing MFA secrets unreadable.
- Rate limiting supports SQLite/PostgreSQL atomic subject locks, but PostgreSQL concurrency testing and trusted-proxy/IP configuration remain unverified.
- Audit records are application-level records; immutability against a database administrator, external log export, retention controls, and tamper-evidence are not implemented.
- Tenant isolation is demonstrated on organization, catalog, supplier-profile, accounting, audit, and buyer-order routes; every new tenant-owned query must enforce its own authorization boundary.
- No payment provider or real payment processing is connected.
- PostgreSQL-specific concurrency, production secrets management, backups, and incident response have not been verified.

## Required before production
- Complete threat modeling and an independent security review.
- Harden atomic rate limiting, add account lockout and MFA recovery codes, define safe secret-key rotation, and validate a complete secure session lifecycle.
- Verify tenant scoping for every new endpoint and add cross-tenant regression tests.
- Protect audit retention and export logs to a separate access-controlled destination.
- Keep secrets in a secret manager; never commit real credentials.
- Add dependency/security scanning, backup/restore exercises, and incident procedures.
- Restrict connector scopes and outbound network access; apply timeouts and budgets.
- Require explicit human approval for high-risk financial or irreversible actions.
- Use sandbox payment credentials for integration tests.
- Require explicit owner approval before paid services or production deployment.

Never call a system secure solely because these requirements are documented.
