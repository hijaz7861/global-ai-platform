# Catalog MVP

## Current API
- `POST /api/v1/organizations/{organization_id}/listings`: authenticated organization member creates a draft listing.
- `GET /api/v1/organizations/{organization_id}/listings`: organization members list their tenant's listings.
- `POST /api/v1/organizations/{organization_id}/listings/{listing_id}/publish`: owner/admin publishes a listing.
- `POST /api/v1/organizations/{organization_id}/listings/{listing_id}/inventory-adjustments`: owner/admin changes stock with an optional reason; stock, append-only before/after history and audit event commit together.
- `GET /api/v1/organizations/{organization_id}/listings/{listing_id}/inventory-adjustments`: owner/admin reads tenant-scoped stock adjustment history.
- `GET /api/v1/listings`: public paginated browse of active listings only, with bounded pagination and currency/price filters.

## Pricing contract
Prices are positive integer minor units. Currency-specific decimal handling needs a dedicated money/currency registry before financial transactions. Never use binary floating-point for money.

## Tenant and inventory boundaries
Tenant listing queries are scoped to the authorized organization. Public browsing exposes active listings only. Order creation uses conditional stock reservation and transaction rollback; multi-item carts reserve all lines atomically, and PostgreSQL CI verifies that concurrent buyers cannot oversell a single inventory unit. Concurrent cancellation is also tested to ensure stock is restored once. These checks do not cover every possible inventory workflow.

## Remaining work
Product variants, image uploads, categories, moderation, seller-verification policy, shipping, payment processing, tax, returns, reviews, marketplace disputes, and broader inventory concurrency/load testing remain future work.
