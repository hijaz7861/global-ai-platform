# Order and Inventory MVP

## API
- `POST /api/v1/orders`: authenticated buyer creates a one-listing order using the legacy `listing_id` and `quantity` fields, or a multi-item order using `items` (1–50 unique listings). Requires an `Idempotency-Key` header (8–128 characters). The response includes `expires_at`; `ORDER_PAYMENT_EXPIRE_MINUTES` defaults to 30.
- `GET /api/v1/orders`: lists only the current buyer's orders.
- `GET /api/v1/orders/{order_id}`: returns an order only to its buyer.
- `POST /api/v1/orders/{order_id}/cancel`: buyer cancels their own unpaid order and releases reserved stock.
- `POST /api/v1/platform/orders/expire-pending`: configured platform admin runs a bounded, idempotent sweep of overdue unpaid orders; the order state transition, stock release and audit event are committed together. Schedule this endpoint through a trusted external scheduler to make expiry run automatically.
- `GET /api/v1/organizations/{organization_id}/orders`: organization members can view order summaries for their own listings only; buyer identity is not exposed.
- Owner/admin can adjust inventory through `POST /api/v1/organizations/{organization_id}/listings/{listing_id}/inventory-adjustments` with a nonzero signed `delta`.

## Inventory safety
Order creation conditionally decrements inventory only when the listing is active and enough stock remains. The conditional update and order insertion are in the same database transaction. If order creation fails, the stock decrement is rolled back. Cancellation is allowed only while the order is `pending_payment`; the transaction changes its state to `cancelled` and restores item quantities. Repeated cancellation is idempotent and does not release stock twice. The expiry sweep conditionally transitions only overdue `pending_payment` orders to `expired`, then restores stock once. A concurrent sweep cannot release stock twice because the order state transition must affect exactly one row. The application exposes the sweep endpoint but does not install a scheduler itself. Manual inventory adjustments require owner/admin role, reject a negative resulting quantity, and create an audit event.

## Idempotency
The database enforces a unique key per buyer. Repeating the same key and same normalized item set returns the existing order without a second stock decrement. Reusing a key for a different item set returns HTTP 409. Stock decrements are applied in stable listing-ID order and occur in the same transaction; if any line lacks stock, all decrements roll back. One order must use a single currency, duplicate listing IDs are rejected, and order totals are bounded to the supported integer range.

## Money and payment boundaries
Amounts are stored as integer minor units; no floating-point arithmetic is used. The order snapshots the title and unit price at creation. Orders are created in `pending_payment` state only. Payment, refunds, seller settlements, taxes, shipping, and accounting postings are not part of order processing. Do not mark an order paid or post financial entries without a verified payment workflow.

## Remaining work
Payment sandbox integration, shipping/tax calculation, refunds, disputes, and database-specific concurrency tests.
