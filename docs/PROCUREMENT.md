# Supplier Procurement and Quotes

## Implemented workflow
- Buyer owner/admin creates and lists tenant-scoped procurement requests.
- Approved supplier owner/admin submits one quote per request; quote currency must match the request.
- Buyer owner/admin compares quotes and awards at most one quote. Awarded quote, rejected competing quotes, request status and audit events commit together.
- Buyers can close open requests; pending quotes are rejected on closure.
- Awarded quotes can be revised through the supported quote-revision history workflow where permitted by state.
- Purchase-order generation from an awarded quote is idempotent. The purchase order records supplier/buyer organization, awarded quote, amount/currency and status; buyer cancellation and supplier fulfilment transitions are supported.
- PostgreSQL and application-level constraints enforce tenant boundaries, approved-supplier eligibility, quote uniqueness and single-winner award semantics.

## Main API
- `POST /api/v1/organizations/{buyer_organization_id}/procurement-requests`: create a request.
- `GET /api/v1/organizations/{buyer_organization_id}/procurement-requests`: list requests with bounded status/limit/offset filters.
- `GET /api/v1/organizations/{buyer_organization_id}/procurement-requests/{request_id}/quotes`: compare quotes.
- `POST /api/v1/organizations/{supplier_organization_id}/procurement-requests/{request_id}/quotes`: submit a quote as an approved supplier.
- `POST /api/v1/organizations/{buyer_organization_id}/procurement-requests/{request_id}/award`: award one quote.
- `POST /api/v1/organizations/{buyer_organization_id}/procurement-requests/{request_id}/close`: close an open request.
- Purchase-order routes are documented in the versioned OpenAPI schema; they remain workflow records and do not move money.

## Remaining work
Procurement approval thresholds, supplier availability/inventory, RFQ attachments, delivery milestones, taxes/freight/landed cost, invoice matching, payment, logistics integrations and broader PostgreSQL race testing remain open. This workflow does not guarantee supplier delivery or execute payments.
