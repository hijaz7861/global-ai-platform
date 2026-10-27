# Production Readiness Gate

> **Current head (2026-10-27):** Production startup validation now fails closed unless `ENVIRONMENT=production` has a `SECRET_KEY` of at least 32 characters and a PostgreSQL URL. Six focused configuration/startup tests passed in an isolated local test slice, not the full repository. CI run [527](https://github.com/hijaz7861/global-ai-platform/actions/runs/40540024932) failed before steps ran: all five jobs report `runner_id: 0`, empty runner names, and `steps: []`. The branch is now synchronized with current `master` (0 commits behind), but full tests and migration/audit checks remain unverified for this head. Do not deploy this branch as production until the full suite, PostgreSQL migrations/integration tests, dependency audit, and operator-owned staging gates below are verified.

> **Current verification note (2026-10-26):** PR #1 includes the environment-template correction and removal of the unused `httpx2` dependency from both dependency manifests. CI run [40338699506](https://github.com/hijaz7861/global-ai-platform/actions/runs/40338699506) for commit `c3b0ad61fdd395ec98a0849bc31c1ef31d32863a` failed across all five jobs; the GitHub job responses expose no steps or runner details, and log retrieval returned `BlobNotFound`. A retry failed the same way. The exact cause remains unconfirmed; do not assume this proves a code failure or that quota is exhausted. Historical green runs apply only to their recorded commits. The current head is not CI-verified.

This checklist is deliberately not marked complete merely because CI passes. Complete each gate in a staging environment with evidence before a production release.

## Current verified automation
- [x] Ruff lint and Python compilation in CI.
- [x] Dependency vulnerability audit (`pip-audit`) has passed in historical CI; the current PR head still requires a successful rerun.
- [x] Bandit static security analysis has passed in historical CI at configured thresholds; the current PR head still requires a successful rerun.
- [x] Alembic upgrade consistency on Python 3.11, 3.12, and 3.13.
- [x] PostgreSQL 16 full migration and schema-consistency job.
- [x] PostgreSQL concurrent refresh-token replay integration test.
- [x] PostgreSQL concurrent single-unit inventory reservation test.
- [x] PostgreSQL concurrent cancellation test verifies inventory is released exactly once.
- [x] PostgreSQL cancellation-versus-expiry race test verifies inventory is released exactly once.
- [x] PostgreSQL concurrent overlapping accounting-period creation test.
- [x] Database triggers reject UPDATE/DELETE of audit events on PostgreSQL and SQLite.
- [x] API tests for authentication, tenant boundaries, orders, procurement, accounting, and security maintenance.

## Operator-owned gates before staging
- [ ] Configure a unique SECRET_KEY (at least 32 random characters) using the deployment secret manager; never commit .env.
- [ ] Configure SMTP and verify registration, email verification, password reset, invitation delivery, and bounce/failure handling with a real test mailbox.
- [ ] Set PLATFORM_ADMIN_EMAILS to explicit authorized operator addresses; test denied access for every other account.
- [ ] Set DATABASE_URL to managed PostgreSQL; confirm TLS, least-privilege DB role, connection limits, and migration permissions.
- [ ] Keep AI_LIVE_CALLS_ENABLED=false and AI_DAILY_REQUEST_LIMIT=0 until a provider, budget, permissions, and approval path are deliberately configured.
- [ ] Configure HTTPS, request size limits, rate limits, and log redaction at the actual ingress. If behind a reverse proxy, set `TRUSTED_PROXY_IPS` to exact trusted proxy IPs/CIDRs; forwarded client-IP headers are ignored unless the direct peer is trusted. Leave it empty for direct connections and never trust broad or user-controlled networks.
- [ ] Schedule the one-shot runner (python -m app.maintenance) with a durable cron/CronJob runner for unpaid-order expiry and security-record cleanup; verify retries, alerting, and idempotency.
- [ ] Configure monitoring for error rate, latency, authentication failures, failed email delivery, database saturation, scheduler lag, and quota rejection.

## Backup and recovery evidence
- [ ] Configure encrypted automated PostgreSQL backups and a documented retention period.
- [ ] Perform a restore into an isolated environment; record restore duration and validate row counts, constraints, migrations, audit events, orders, and ledger balances.
- [ ] Document recovery point objective (RPO), recovery time objective (RTO), owner, escalation contact, and rollback steps.
- [ ] Test rollback/forward-fix procedures against a disposable database copy; do not assume every data migration is safely reversible.

## Security and domain sign-off
- [ ] Conduct an independent security review and threat-model walkthrough, including MFA recovery, session revocation, tenant isolation, admin allowlists, and trusted-proxy client IP handling. CI tests cover untrusted forwarded headers, trusted proxy chains, and malformed forwarded values.
- [ ] Define signing-key rotation procedure: configure the new `SECRET_KEY` and old key in `PREVIOUS_SECRET_KEYS`, run `python -m app.rotate_mfa_secrets`, verify MFA login and the reported re-encryption count, retain the old key until prior access tokens expire, then remove it. Test this in staging first.
- [ ] Review audit retention, export to a separately controlled destination, and tamper-evidence. Append-only triggers are not protection against a privileged operator who can disable triggers.
- [ ] Obtain accounting-domain review of period policy, financial statement semantics, multi-currency limitations, and reconciliation controls.
- [ ] Define payment/refund/tax/seller-settlement scope before connecting a real payment provider; no money movement is enabled by this foundation.
- [ ] Run staged load/concurrency tests against a production-like PostgreSQL configuration and record results.
- [ ] Confirm privacy policy, terms, data retention/deletion, incident response, and user support ownership.

## Release decision
A release owner must review the evidence above and explicitly approve the release. Passing CI is necessary but not sufficient. Do not enable paid AI calls, real payment processing, or production deployment without explicit authorization.
- [x] PostgreSQL CI verifies concurrent bank-reconciliation window creation and database-trigger rejection of overlapping statement windows; broader reconciliation race coverage remains required.

- [x] SQLite/API tests cover reconciliation correction, unmatch/rematch, and completed-session immutability.

- [x] PostgreSQL integration test verifies concurrent reconciliation matches cannot reuse one journal line.

- [x] PostgreSQL CI verifies migration 0027's journal-entry period trigger, posting-versus-closure race, and rejection of direct SQL inserts into a closed period.

- [x] PostgreSQL integration verifies concurrent procurement awards accept exactly one supplier quote.
