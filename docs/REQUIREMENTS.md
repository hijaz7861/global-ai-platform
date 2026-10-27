# Requirements and delivery plan

## User roles to define
- Visitor
- Customer / buyer
- Seller / supplier
- Service provider
- Educator / learner
- Business operator
- Tenant administrator
- Platform administrator
- Auditor / compliance reviewer

Roles are requirements, not implemented permissions.

## Core requirements
1. Authentication and role-based authorization.
2. Tenant isolation and organization membership.
3. Product, service and marketplace catalogues.
4. Orders, fulfilment, returns and dispute workflows.
5. Supplier verification and logistics integrations.
6. Rental and property listings.
7. Jobs, professional services and education.
8. Auditable accounting ledger and reconciliation.
9. AI provider routing with budget and usage limits.
10. Connector registry with scoped permissions.
11. Consent, privacy, retention and data export/deletion workflows. **Partial:** authenticated self-service export covers basic profile data, organization memberships and buyer orders; it excludes credential material and other users' records. Export tests are committed but await a verified full-suite run. Consent, retention and safe deletion/anonymization remain open.
12. Regional tax, consumer protection and compliance review.
13. Monitoring, backups, recovery and automated tests.

## Implementation gates
- Gate 1: validate user needs and data model.
- Gate 2: implement authentication and tenant isolation.
- Gate 3: build one end-to-end marketplace workflow.
- Gate 4: add accounting and audit controls.
- Gate 5: add AI and external connectors behind permissions.
- Gate 6: security, integration and regression testing.
- Gate 7: staging review; production only after explicit approval.

Do not claim a requirement is complete until implementation and tests
provide evidence.
