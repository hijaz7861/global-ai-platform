# Order and Inventory MVP

## API
- `POST /api/v1/orders`: authenticated buyer creates a one-listing order using legacy `listing_id` and `quantity`, or a multi-item order using `items` (1–50 unique listings). Requires `Idempotency-Key` (8–128 characters). Response includes `expires_at`; `ORDER_PAYMENT_EXPIRE_MINUTES` defaults to 30.
- `GET /api/v1/orders` and `GET /api/v1/orders/{order_id}`: buyer-scoped order reads.
- `POST /api/v1/orders/{order_id}/cancel`: buyer cancels their own unpaid order and releases stock once.
- `POST /api/v1/platform/orders/expire-pending`: configured platform admin runs a bounded idempotent expiry sweep; a trusted external scheduler is required for recurring execution.
- `GET /api/v1/organizations/{organization_id}/orders`: seller-scoped order summaries without exposing buyer identity.
- Owner/admin inventory adjustments use `POST /api/v1/organizations/{organization_id}/listings/{listing_id}/inventory-adjustments` with a nonzero signed `delta`.

## Inventory safety
Order creation conditionally decrements stock only when the listing is active and enough stock remains. Multi-item decrements occur in stable listing-ID order in the same transaction; if any line fails, all decrements roll back. Cancellation uses a conditional pending-to-cancelled transition as a single-winner gate before restoring stock. The expiry sweep conditionally transitions only overdue pending orders before releasing stock. PostgreSQL CI tests concurrent reservation, concurrent cancellation, and cancellation racing the expiry sweep to verify no oversell and no double stock release. Broader order/expiry/inventory concurrency and load testing remain open.

## Idempotency
The database enforces a unique key per buyer. Repeating the same key and same normalized item set returns the existing order without a second stock decrement. Reusing a key for a different item set returns HTTP 409. One order must use a single currency, duplicate listing IDs are rejected, and order totals are bounded to the supported integer range.

## Money and payment boundaries
Amounts are stored as integer minor units; no floating-point arithmetic is used. The order snapshots title and unit price at creation. Orders remain `pending_payment`; payment, refunds, seller settlements, taxes, shipping and accounting postings are not part of order processing. Do not mark an order paid or post financial entries without a verified payment workflow.

## Remaining work
Payment sandbox integration, shipping/tax calculation, refunds, disputes, recurring scheduler provisioning, monitoring/alerting, and broader database-specific concurrency/load tests.
