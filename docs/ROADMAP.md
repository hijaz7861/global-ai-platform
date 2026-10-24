# Delivery Roadmap

1. **Foundation — in draft:** FastAPI API, health contract, CI, database migration workflow, linting.
2. **Identity and initial tenancy — initial slice implemented, hardening remains:** users, password hashing, short-lived bearer access tokens, rotating hashed refresh tokens with replay detection and family revocation, persistent login/password-reset request rate limits, organizations, owner/admin membership management, tenant-scoped access.
3. **Catalog MVP — initial slice implemented:** listings, draft/active publication, public search and currency/price filters, bounded pagination, minor-unit pricing.
4. **Order/inventory MVP — initial slice implemented:** single-listing orders, conditional stock reservation, per-buyer idempotency, buyer-scoped reads, unpaid-order cancellation, stock release, owner/admin inventory adjustments with append-only before/after history and audit events, seller-scoped order summaries, and platform-admin-triggered idempotent expiry with stock release.
5. **Accounting ledger — initial slice implemented:** tenant-scoped chart of accounts, balanced journal posting, immutable one-time counter-entry reversals, derived trial balance, currency/account checks, idempotency, and audit events.
6. **Accounting period controls — initial slice implemented:** organization-scoped non-overlapping periods, owner/admin listing and creation, audited one-way closure, and guards against new journal postings/reversals outside an open period once periods are configured. Period-specific reports and database-level overlap/concurrency enforcement remain open.
7. **AI adapter — guarded initial slice:** OpenAI-compatible endpoint adapter, output caps, timeout, audit events, opt-in and request quota. Multi-provider routing and agents remain future work.
8. **Commerce hardening — next:** multi-item carts, external scheduler provisioning for order expiry, database concurrency tests, and full inventory/order race-condition coverage.
9. **Financial hardening — next:** period-specific account balances/reports, reconciliation, approvals, and payment sandbox.
10. **Supplier operations — initial slice implemented:** organization-owned supplier profile, submission, platform-admin review, rejection reason and resubmission. Quotes, procurement and fulfilment remain next.
11. **Logistics and fulfilment:** shipping quotes, tracking, returns and disputes.
12. **AI orchestration:** provider registry, budgets, tool permissions, audit, human approval and bounded retries.
13. **Connector hub:** OAuth lifecycle, scoped tokens, webhook signature verification, retry handling and revocation.
14. **Expansion modules:** rentals, real estate, workforce, education and business operations.
15. **Readiness:** threat model, integration/regression tests, observability, backup restore, staging review.

Identity hardening still needs MFA, expired-token cleanup, and concurrency-safe rate limiting. Refresh-token rotation is covered by CI, including replay detection, logout family revocation, and expired consumed-token replay. Every phase needs acceptance criteria and automated tests. Production deployment requires explicit owner approval. CI passing is not equivalent to production readiness.
