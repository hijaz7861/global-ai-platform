# Delivery Roadmap

1. **Foundation — in draft:** FastAPI API, health contract, CI, database migration workflow.
2. **Identity and initial tenancy — implemented, hardening remains:** users, password hashing, bearer tokens, organizations, memberships, tenant-scoped access.
3. **Catalog MVP — initial slice implemented:** listings, draft/active publication, public active browse, minor-unit pricing.
4. **Order/inventory MVP — initial slice implemented:** single-listing orders, conditional stock reservation, per-buyer idempotency, buyer-scoped reads, unpaid-order cancellation, stock release, owner/admin inventory adjustments, seller-scoped order summaries.
5. **Accounting ledger — initial slice implemented:** tenant-scoped chart of accounts, balanced journal posting, currency/account checks, idempotency, and audit events.
6. **Commerce hardening — next:** multi-item carts, inventory adjustment history, order expiry, database concurrency tests.
7. **Financial hardening:** reversals, period controls, account balances/reports, reconciliation, approvals, and payment sandbox.
8. **Supplier operations:** onboarding, verification, quotes, procurement and fulfilment.
9. **Logistics and fulfilment:** shipping quotes, tracking, returns and disputes.
10. **AI orchestration:** provider registry, budgets, tool permissions, audit, human approval and bounded retries.
11. **Connector hub:** OAuth lifecycle, scoped tokens, webhook signature verification, retry handling and revocation.
12. **Expansion modules:** rentals, real estate, workforce, education and business operations.
13. **Readiness:** threat model, integration/regression tests, observability, backup restore, staging review.

Every phase needs acceptance criteria and automated tests. Production deployment requires explicit owner approval. CI passing is not equivalent to production readiness.
