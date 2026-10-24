# Catalog MVP

## Current API
- `POST /api/v1/organizations/{organization_id}/listings`: authenticated organization member creates a draft listing.
- `GET /api/v1/organizations/{organization_id}/listings`: organization members can list that tenant's listings.
- `POST /api/v1/organizations/{organization_id}/listings/{listing_id}/publish`: owner/admin publishes a listing.
- `POST /api/v1/organizations/{organization_id}/listings/{listing_id}/inventory-adjustments`: owner/admin changes stock with an optional reason; the stock change, append-only adjustment history, and audit event commit together.
- `GET /api/v1/organizations/{organization_id}/listings/{listing_id}/inventory-adjustments`: owner/admin reads tenant-scoped stock adjustment history in reverse chronological order.
- `GET /api/v1/listings`: public paginated browse of active listings only; supports `limit` (1–100) and `offset` (0 or greater).

## Pricing contract
Prices are positive integer minor units. Currency-specific decimal handling needs a dedicated money/currency registry before financial transactions. Never use binary floating-point for money.

## Tenant boundaries
Tenant listing queries filter by both listing ID (where applicable) and the authorized membership's organization ID. Public browsing intentionally exposes only active listings. All future tenant-owned resources must repeat the same authorization discipline.

## Not yet implemented
Inventory adjustment history is implemented; stock availability is still not reserved for every possible concurrent workflow. Product variants, images/uploads, categories, moderation, seller verification, multi-item carts, shipping, payment processing, tax, returns, reviews, and marketplace disputes remain future work.
