# Delivery Roadmap

1. **Foundation — in draft:** FastAPI API, health contract, CI, database migration workflow.
2. **Identity and initial tenancy — implemented, hardening remains:** users, password hashing, bearer tokens, organizations, memberships, tenant-scoped access.
3. **Catalog MVP — initial slice implemented:** listings, draft/active publication, public active browse, minor-unit pricing.
4. **Commerce transactions — next:** cart/order lifecycle, stock reservations, idempotency, cancellation and refund states.
5. **Supplier operations:** onboarding, verification, quotes, procurement and fulfilment.
6. **Finance controls:** immutable double-entry journal, idempotency, invoices, commissions and reconciliation.
7. **Logistics and fulfilment:** shipping quotes, tracking, returns and disputes.
8. **AI orchestration:** provider registry, budgets, tool permissions, audit, human approval and bounded retries.
9. **Connector hub:** OAuth lifecycle, scoped tokens, webhook signature verification, retry handling and revocation.
10. **Expansion modules:** rentals, real estate, workforce, education and business operations.
11. **Readiness:** threat model, integration/regression tests, observability, backup restore, staging review.

Every phase needs acceptance criteria and automated tests. Production deployment requires explicit owner approval. CI passing is not equivalent to production readiness.
