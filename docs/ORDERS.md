# Order and Inventory MVP

## API
- `POST /api/v1/orders`: authenticated buyer creates a one-listing order. Requires an `Idempotency-Key` header (8–128 characters).
- `GET /api/v1/orders`: lists only the current buyer's orders.
- `GET /api/v1/orders/{order_id}`: returns an order only to its buyer.
- `POST /api/v1/orders/{order_id}/cancel`: buyer cancels their own unpaid order and releases reserved stock.
- Listing owners/admins publish listings; listing creation may set a nonnegative inventory quantity.
- Public listing browse only returns active listings.

## Inventory safety
Order creation conditionally decrements inventory only when the listing is active and enough stock remains. The conditional update and order insertion are in the same database transaction. If order creation fails, the stock decrement is rolled back. Cancellation is allowed only while the order is `pending_payment`; the transaction changes its state to `cancelled` and restores the item quantities. Repeated cancellation is idempotent and does not release stock twice. This reduces overselling risk; concurrency and locking behavior still need dedicated tests on the intended production database.

## Idempotency
The database enforces a unique key per buyer. Repeating the same key and same listing/quantity returns the existing order without a second stock decrement. Reusing a key for a different listing or quantity returns HTTP 409.

## Money and payment boundaries
Amounts are stored as integer minor units; no floating-point arithmetic is used. The order snapshots the title and unit price at creation. The order total is computed from the snapshot. Orders are created in `pending_payment` state only. Payment, refunds, seller settlements, taxes, shipping, and accounting postings are not implemented. Do not mark an order paid or post financial entries without a verified payment workflow.

## Remaining work
Multi-line carts, inventory adjustment permissions, seller-side order management, cancellation expiry, payment sandbox integration, shipping/tax calculation, refunds, disputes, and database-specific concurrency tests.
