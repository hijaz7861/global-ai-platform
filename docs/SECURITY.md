# Security Baseline

## Implemented controls in the draft foundation
- Passwords are hashed with Argon2; bearer-token issuance fails closed unless `SECRET_KEY` has at least 32 characters.
- Short-lived access tokens use hashed, rotating refresh tokens. Reuse of a consumed refresh token revokes its session family; logout revokes the session family and access tokens.
- TOTP MFA secrets are encrypted with AES-GCM using a key derived from `SECRET_KEY`; MFA supports hash-only single-use recovery codes, password reauthentication for enrollment/disable, and replay protection.
- Previous signing keys can validate existing tokens during a controlled rotation. `python -m app.rotate_mfa_secrets` re-encrypts stored MFA secrets; this procedure still requires staging validation.
- Email verification and password-reset tokens are random, single-use, expiring, and stored as SHA-256 digests. Generic responses reduce account enumeration; SMTP delivery is configurable.
- Login, registration, password reset, email verification and related identity requests use persistent rate limiting. Atomic subject locks are implemented for SQLite/PostgreSQL; account lockout is persistent. Forwarded client IPs are accepted only when the direct peer matches explicitly configured `TRUSTED_PROXY_IPS`.
- Organization, membership, supplier, procurement, order, inventory, and accounting endpoints enforce tenant-specific access checks; cross-tenant regression tests cover key flows.
- Orders use conditional inventory updates, transaction boundaries, per-user idempotency, multi-item validation, cancellation and expiry stock release. PostgreSQL tests cover oversell prevention and concurrent cancellation.
- Procurement enforces approved-supplier quote submission, tenant boundaries, a single awarded quote, and transactional purchase-order issue/fulfilment states.
- Journal entries are balanced, idempotent and immutable; corrections use one-time reversing entries. Period overlap is blocked by application checks and PostgreSQL/SQLite triggers.
- Audit events are allowlisted and transaction-bound for selected sensitive operations. Migration 0024 adds database triggers rejecting UPDATE/DELETE of audit rows on PostgreSQL and SQLite.
- AI calls are disabled by default, restricted to organization owners/admins, require explicit configuration and PostgreSQL quota serialization, cap response sizes, reject redirects, and avoid logging prompts, credentials or provider response bodies.
- Security maintenance and unpaid-order expiry are available through the bounded one-shot `python -m app.maintenance` runner. Recurring scheduling and alerting are operator-owned.
- CI runs Ruff, compilation, Alembic consistency, pytest on Python 3.11–3.13, PostgreSQL 16 integration tests, dependency auditing, and Bandit.

## Verification evidence
- CI run 460 at commit `5257b65ffe730f93eb8102797abefa75fad2a58a`: 83 passed and 8 environment-dependent tests skipped on each Python matrix job; PostgreSQL 16 integration job 8 passed, including concurrent cancellation/expiry and database-enforced non-overlapping reconciliation windows; dependency audit found no known vulnerabilities and Bandit reported no issues at configured thresholds.
- Static checks and CI are not an independent security audit and do not prove production configuration or deployment readiness.

## Current limitations
- Multi-provider AI routing/fallback, monetary budgets, tool permissions, and autonomous agent workflows are not implemented.
- Payment processing, refunds, tax, seller settlement and financial reconciliation are not implemented.
- Audit triggers do not protect against a privileged database operator disabling them. Cryptographic hash chaining, external log export and retention policy remain open.
- PostgreSQL concurrency coverage is improving but does not cover every order, inventory, accounting and procurement race.
- Signing-key rotation and MFA secret re-encryption need staging validation.
- SMTP, trusted proxy CIDRs, production secrets, scheduler, monitoring, backups and incident response are not configured by CI.
- Tenant isolation must be reviewed for every newly added endpoint and connector.

## Required before production
- Complete threat modeling and an independent security review, including MFA recovery, session revocation, admin allowlists, trusted proxies, tenant isolation and high-risk action approval.
- Validate key rotation and MFA login in staging.
- Configure managed PostgreSQL with TLS, least-privilege roles, connection limits and protected migration credentials.
- Configure SMTP and verify email, password-reset and invitation delivery/failure behavior with real test mailboxes.
- Keep live AI disabled until provider budget, permissions and approval controls are reviewed.
- Configure HTTPS, ingress limits, log redaction, trusted proxy IPs/CIDRs, monitoring and alerting.
- Schedule and monitor `python -m app.maintenance`; verify retry behavior and idempotency.
- Configure encrypted backups, retention, isolated restore drills, RPO/RTO, rollback and incident procedures.
- Review accounting semantics, reconciliation, payment/refund/tax scope and audit export/retention.
- Run production-like PostgreSQL load/concurrency tests and verify privacy, terms, retention/deletion, support and incident ownership.
- Require explicit owner approval before paid services, real payment processing or production deployment.

Never call a system secure solely because these requirements are documented or CI passes.
