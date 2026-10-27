# Implementation Status

## Implemented in the draft foundation branch
- FastAPI application, OpenAPI, versioned health endpoint, SQLAlchemy models, Alembic migrations, and optional Docker packaging that applies migrations before startup.
- Identity: normalized registration, Argon2 password hashing, email verification, password reset, rotating hashed refresh tokens with replay-family revocation, logout revocation, persistent request throttling, account lockout, encrypted TOTP MFA, single-use recovery codes, organization memberships, and expiring email invitations.
- Security controls: fail-closed signing-key validation, controlled previous-key verification and MFA-secret re-encryption utility, trusted-proxy-only forwarded client IP handling, allowlisted audit details, database-enforced append-only audit events, and bounded security cleanup.
- Commerce: tenant-scoped catalog, public active-listing search with bounded filters/pagination, multi-item carts up to 50 unique listings, conditional stock reservation, idempotency, buyer-scoped reads, seller summaries, cancellation, unpaid expiry, inventory adjustments with append-only before/after history, and a bounded idempotent maintenance runner.
- Suppliers/procurement: supplier onboarding and admin review, approved-supplier quotes, quote revisions/withdrawals, single-winner award, purchase-order issue with idempotent replay, buyer cancellation, and supplier fulfilment.
- Accounting: tenant-scoped chart of accounts, balanced idempotent double-entry journal posting, immutable one-time reversals, accounting periods with database-enforced non-overlap, period/all-time trial balances, paginated account ledger, income statement, and balance sheet grouped by currency.
- Manual bank reconciliation: tenant-scoped statement sessions for active asset accounts, signed statement-line entry, exact one-to-one matching against posted journal lines, single-use journal-line matching, balance arithmetic validation, correction/unmatch/rematch/delete workflows while open, and immutable completed sessions. PostgreSQL/SQLite triggers prevent overlapping statement windows.
- Guarded OpenAI-compatible adapter: explicit opt-in, primary/fallback endpoint selection, output/time limits, response-size cap, no automatic retries, safe errors, provider/model audit metadata, PostgreSQL-serialized request quotas, and live calls disabled by default.
- CI: Ruff, compilation, Alembic migration consistency, pytest on Python 3.11–3.13, PostgreSQL 16 integration tests, pip-audit, and Bandit.

## Recent reliability improvement (pending CI)
- Added `GET /api/v1/ready`, which executes a lightweight `SELECT 1` against the configured database and returns HTTP 503 with a generic message if SQLAlchemy reports a database error. The existing `/api/v1/health` remains process-level health only.
- Added tests for successful readiness and fail-closed behavior without leaking database exception details. These changes are committed but remain unverified until the current-head test suite passes.

## Verification status

> **Current head status (2026-10-26):** The environment-template correction and removal of the unused `httpx2` dependency from both dependency manifests are committed. CI run [40338699506](https://github.com/hijaz7861/global-ai-platform/actions/runs/40338699506) for commit `c3b0ad61fdd395ec98a0849bc31c1ef31d32863a` failed across all five jobs; job step and runner details were absent, log retrieval returned `BlobNotFound`, and a retry failed the same way. The exact cause remains unconfirmed; this does not establish whether the workflow failed because of runner infrastructure, account configuration, or code. Historical green runs verify only their recorded commits, not the current head. Do not mark the current head as tested until the full workflow completes.
- CI run 479 at commit `d11aa8dec6d95afedaed09c1e8e70f4933c22b96` passed Ruff, compilation, Alembic consistency, and 83 passed / 11 environment-dependent skips on each of Python 3.11, 3.12, and 3.13. PostgreSQL 16 integration reported 11 passed, including concurrent procurement quote awards with exactly one accepted quote. Dependency audit found no known vulnerabilities and Bandit reported no issues at configured thresholds.
- The automated checks are not an independent security audit and do not prove production configuration. No production deployment, live payment processing, or live paid AI request has occurred.

## Important remaining gaps
- Concurrent matching, posting/closure, and procurement-award tests passed; broader PostgreSQL workload/race coverage remains open across inventory, order expiry, accounting, reconciliation, and procurement.
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


## Reconciliation CSV import (pending CI)
- Added migration `0028_reconciliation_imports` and a provenance table storing the importing actor, sanitized source filename, row count, creation time, and SHA-256 of the exact uploaded bytes.
- Added a strict UTF-8 raw-CSV endpoint with exact column order, ISO date parsing, integer minor-unit amounts, a 1 MB / 1,000-row limit, statement-window checks, duplicate-reference checks, and all-rows-validated-before-write behavior.
- The batch and its statement lines are committed atomically; same-file replay returns the existing batch without duplicate lines. Manual lines remain distinguishable via nullable `import_batch_id`.
- Tests cover import provenance, replay/idempotency, row-level validation rollback, duplicate references, and malformed headers.
- CI run 493 passed: Python 3.11, 3.12 and 3.13 each reported 85 passed and 11 environment-dependent skips; PostgreSQL 16 integration reported 11 passed; dependency audit reported no known vulnerabilities and Bandit reported no issues at configured thresholds. The CSV import slice is CI-verified, but still needs bank-format validation, staging review, and finance-domain sign-off before production use.


## CI run 493 verification
- Head commit: `bf816cd7a872d67b5b774b85302ec8e6d41093fc`.
- Python 3.11, 3.12 and 3.13: each 85 passed, 11 environment-dependent skips.
- PostgreSQL 16 integration: 11 passed.
- Ruff, compilation, Alembic consistency, dependency audit and Bandit passed at configured thresholds.
- This verifies the CSV import code in CI, not production bank compatibility or independent accounting/security approval.

## Own Automation Engine integration (2026-10-27)

- Added `docs/OWN_AUTOMATION_ENGINE_INTEGRATION.md` and linked it from the README.
- Confirmed the engine's MCP PR #14 is merged to its `main` branch. Its MCP surface is local stdio and exposes discovery/inspection/design/proposal tools only; workflow apply/execute are intentionally unavailable.
- Defined a phased integration contract: platform-owned identity/tenant scope, read-only discovery, proposal-only changes, secure tunnel transport, server-side approval records, audit redaction, idempotency, and later guarded execution.
- **Not connected:** no live tunnel ID/runtime key or end-to-end handshake has been verified. No engine calls are made by Global AI Platform yet. This commit documents the integration boundary only; it does not claim runtime integration.
- **Release gate:** do not merge/deploy or enable execution based on this document alone. Verify current-head CI and complete the secure transport and tenant-isolation tests first.
- Added `app/automation/contracts.py` with strict Pydantic schemas and an allowlist for the engine's six discovery/design/proposal tools. Unknown tools (including `workflow.execute`) and caller-supplied approval/tenant fields are rejected; workflow paths and goal lengths are bounded.
- Added `tests/test_automation_contracts.py`. Targeted isolated test harness using the new contract and test logic: **8 passed**. This was not a full repository run and is not a substitute for current-head GitHub Actions CI.
