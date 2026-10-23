# Order and Inventory MVP

## API
- `POST /api/v1/orders`: authenticated buyer creates a one-listing order. Requires an `Idempotency-Key` header (8–128 characters).
- `GET /api/v1/orders`: lists only the current buyer's orders.
- `GET /api/v1/orders/{order_id}`: returns an order only to its buyer.
- `POST /api/v1/orders/{order_id}/cancel`: buyer cancels their own unpaid order and releases reserved stock.
- `GET /api/v1/organizations/{organization_id}/orders`: organization members can view order summaries for their own listings only; buyer identity is not exposed.
- Owner/admin can adjust inventory through `POST /api/v1/organizations/{organization_id}/listings/{listing_id}/inventory-adjustments` with a nonzero signed `delta`.

## Inventory safety
Order creation conditionally decrements inventory only when the listing is active and enough stock remains. The conditional update and order insertion are in the same database transaction. If order creation fails, the stock decrement is rolled back. Cancellation is allowed only while the order is `pending_payment`; the transaction changes its state to `cancelled` and restores item quantities. Repeated cancellation is idempotent and does not release stock twice. Manual inventory adjustments require owner/admin role, reject a negative resulting quantity, and create an audit event.

## Idempotency
The database enforces a unique key per buyer. Repeating the same key and same listing/quantity returns the existing order without a second stock decrement. Reusing a key for a different listing or quantity returns HTTP 409.

## Money and payment boundaries
Amounts are stored as integer minor units; no floating-point arithmetic is used. The order snapshots the title and unit price at creation. Orders are created in `pending_payment` state only. Payment, refunds, seller settlements, taxes, shipping, and accounting postings are not part of order processing. Do not mark an order paid or post financial entries without a verified payment workflow.

## Remaining work
Multi-line carts, inventory adjustment history, order expiry, payment sandbox integration, shipping/tax calculation, refunds, disputes, and database-specific concurrency tests.
