# Delivery Roadmap

1. **Foundation — in draft:** FastAPI API, health contract, CI, database migration workflow.
2. **Identity and initial tenancy — implemented, hardening remains:** users, password hashing, bearer tokens, organizations, memberships, tenant-scoped access.
3. **Catalog MVP — initial slice implemented:** listings, draft/active publication, public active browse, minor-unit pricing.
4. **Order/inventory MVP — initial slice implemented:** single-listing orders, conditional stock reservation, per-buyer idempotency, buyer-scoped reads, unpaid-order cancellation and stock release.
5. **Commerce hardening — next:** multi-item carts, inventory adjustment permissions, seller order views, order expiry, database concurrency tests.
6. **Supplier operations:** onboarding, verification, quotes, procurement and fulfilment.
7. **Finance controls:** immutable double-entry journal, idempotency, invoices, commissions and reconciliation.
8. **Logistics and fulfilment:** shipping quotes, tracking, returns and disputes.
9. **AI orchestration:** provider registry, budgets, tool permissions, audit, human approval and bounded retries.
10. **Connector hub:** OAuth lifecycle, scoped tokens, webhook signature verification, retry handling and revocation.
11. **Expansion modules:** rentals, real estate, workforce, education and business operations.
12. **Readiness:** threat model, integration/regression tests, observability, backup restore, staging review.

Every phase needs acceptance criteria and automated tests. Production deployment requires explicit owner approval. CI passing is not equivalent to production readiness.
