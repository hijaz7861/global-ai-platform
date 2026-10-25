# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, OpenAPI, versioned health endpoint, SQLAlchemy models, Alembic migrations, and optional Docker packaging that applies migrations before startup.
- Identity: normalized registration, Argon2 password hashing, email verification, password reset, rotating hashed refresh tokens with replay-family revocation, logout revocation, persistent request throttling, account lockout, encrypted TOTP MFA, single-use recovery codes, organization memberships, and expiring email invitations.
- Security controls: fail-closed signing-key validation, controlled previous-key verification and MFA-secret re-encryption utility, trusted-proxy-only forwarded client IP handling, allowlisted audit details, database-enforced append-only audit events, and bounded security cleanup.
- Commerce: tenant-scoped catalog, public active-listing search with bounded filters/pagination, multi-item carts up to 50 unique listings, conditional stock reservation, idempotency, buyer-scoped reads, seller summaries, cancellation, unpaid expiry, inventory adjustments with append-only before/after history, and a bounded idempotent maintenance runner.
- Suppliers/procurement: supplier onboarding and admin review, approved-supplier quotes, quote revisions/withdrawals, single-winner award, purchase-order issue with idempotent replay, buyer cancellation, and supplier fulfilment.
- Accounting: tenant-scoped chart of accounts, balanced idempotent double-entry journal posting, immutable one-time reversals, accounting periods with database-enforced non-overlap, period/all-time trial balances, paginated account ledger, income statement, and balance sheet grouped by currency.
- Manual bank reconciliation: tenant-scoped statement sessions for active asset accounts, signed statement-line entry, exact one-to-one matching against posted journal lines, single-use journal-line matching, balance arithmetic validation, correction/unmatch/rematch/delete workflows while open, and immutable completed sessions. PostgreSQL/SQLite triggers prevent overlapping statement windows.
- Guarded OpenAI-compatible adapter: explicit opt-in, output/time limits, response-size cap, no automatic retries, safe errors, audit events, PostgreSQL-serialized quota checks, and live calls disabled by default.
- CI: Ruff, compilation, Alembic migration consistency, pytest on Python 3.11–3.13, PostgreSQL 16 integration tests, pip-audit, and Bandit.

## Verification status
- CI run 465 at commit `32e3426c4598e7f933820762af6f25ccb603d11f`: each Python 3.11/3.12/3.13 job reported 83 passed and 9 environment-dependent skips. PostgreSQL 16 integration reported 9 passed, including concurrent attempts to match one journal line to two statement lines (one succeeds; the other receives HTTP 409). Dependency audit found no known vulnerabilities and Bandit reported no issues at configured thresholds.
- The automated checks are not an independent security audit and do not prove production configuration. No production deployment, live payment processing, or live paid AI request has occurred.

## Important remaining gaps
- The concurrent matching test must pass; broader PostgreSQL workload/race coverage remains open across inventory, order expiry, accounting posting/closure, reconciliation, and procurement.
- Production validation is required for signing-key rotation, MFA re-encryption, SMTP, trusted proxy IP/CIDRs, managed PostgreSQL, secrets management, scheduler provisioning, monitoring/alerting, encrypted backup/restore, incident response, and staged load testing.
- Independent security review, threat-model walkthrough, tenant-isolation review, audit retention/hash chaining/external log export, and accounting-domain validation remain open.
- Accounting gaps include cash-flow statements, payment/bank-feed reconciliation, payment processing, refunds, tax, seller settlement, close/roll-forward workflow, and foreign exchange. The manual bank-statement reconciliation MVP does not connect to banks or move money.
- AI gaps include multi-provider routing/fallback, provider-native APIs, monetary budgets, tool permissions, autonomous agents, and human approval workflows.
- Remaining product domains include logistics, rentals, real estate, workforce/education, OAuth/webhook connectors, and broader role administration.

## Next engineering gates
1. Verify concurrent reconciliation matching and expand critical PostgreSQL race tests.
2. Add reconciliation import provenance/file parsing and finance-domain validation before treating it as production accounting.
3. Add multi-provider AI routing with budget controls, tool permissions, and approval gates.
4. Complete threat modeling, independent review, monitoring, backup/restore drills, and staging release evidence.

CI passing is not equivalent to production readiness. Production deployment and paid services require explicit owner approval.
