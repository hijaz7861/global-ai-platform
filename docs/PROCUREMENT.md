# Supplier Procurement and Quotes

## Scope
This is an initial quote-comparison workflow. It does not create purchase orders, move money, reserve supplier inventory, or guarantee delivery.

## API
- `POST /api/v1/organizations/{buyer_organization_id}/procurement-requests`: owner/admin creates an open request with title, description, quantity, and three-letter currency.
- `GET /api/v1/organizations/{buyer_organization_id}/procurement-requests`: owner/admin lists requests in that buyer organization; optional `status`, `limit`, and `offset` filters.
- `GET /api/v1/organizations/{buyer_organization_id}/procurement-requests/{request_id}/quotes`: owner/admin compares quotes for a request in their own organization, ordered by unit price and lead time.
- `POST /api/v1/organizations/{supplier_organization_id}/procurement-requests/{request_id}/quotes`: owner/admin from an approved supplier organization submits one quote for an open request. Quote currency must match the request currency; each supplier organization may quote once per request.
- `POST /api/v1/organizations/{buyer_organization_id}/procurement-requests/{request_id}/award`: buyer owner/admin accepts one submitted quote and rejects the other submitted quotes in the same transaction.
- `POST /api/v1/organizations/{buyer_organization_id}/procurement-requests/{request_id}/close`: closes an open request and rejects pending submitted quotes. Closing an awarded request is not allowed.

## State and access rules
- Buyer operations are tenant-scoped to the buyer organization in the URL and require owner/admin membership.
- Supplier quote submission requires owner/admin membership in the supplier organization and an approved `SupplierProfile`.
- Only open requests accept quotes; self-quoting on a buyer organization's own request is rejected.
- Database constraints enforce one quote per supplier organization per request, positive unit price, nonnegative lead time, valid status values, and valid organization/user references.
- Award uses a conditional open-to-awarded transition so a request cannot be awarded twice. Accepted quote, rejected competing quotes, request state, and audit events are committed together.
- This workflow records the quote's unit price, currency, lead time, and notes. It does not calculate taxes, freight, exchange rates, or landed cost.

## Remaining work
Purchase-order generation, quote revisions/withdrawals, RFQ attachments, delivery milestones, supplier inventory/availability, approval thresholds, taxes/freight, payment, invoice matching, and PostgreSQL concurrency/integration tests remain out of scope for this slice.
