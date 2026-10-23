# Delivery Roadmap

1. **Foundation:** FastAPI app, API versioning, health contract, tests, CI.
2. **Identity and tenancy:** users, organizations, memberships, roles, tenant isolation.
3. **Core commerce MVP:** catalog, listings, inventory, cart/order lifecycle.
4. **Supplier operations:** onboarding, verification, quotes, procurement.
5. **Finance controls:** double-entry journal, idempotency, invoices, reconciliation.
6. **Logistics and fulfilment:** shipping quotes, tracking, returns, disputes.
7. **AI orchestration:** provider registry, budgets, tool permissions, audit, human approval.
8. **Connector hub:** OAuth lifecycle, scoped tokens, webhooks, signature verification, retry handling.
9. **Expansion modules:** rentals, real estate, workforce, education, business operations.
10. **Readiness:** threat model, integration/regression tests, observability, backup restore, staging review.

Every phase must have explicit acceptance criteria and automated tests. Do not build all domains as shallow placeholders. Production deployment requires explicit owner approval.
