# Catalog MVP

## Current API
- `POST /api/v1/organizations/{organization_id}/listings`: authenticated organization member creates a draft listing.
- `GET /api/v1/organizations/{organization_id}/listings`: organization members can list that tenant's listings.
- `POST /api/v1/organizations/{organization_id}/listings/{listing_id}/publish`: owner/admin publishes a listing.
- `GET /api/v1/listings`: public paginated browse of active listings only; supports `limit` (1–100) and `offset` (0 or greater).

## Pricing contract
Prices are positive integer minor units. Currency-specific decimal handling needs a dedicated money/currency registry before financial transactions. Never use binary floating-point for money.

## Tenant boundaries
Tenant listing queries filter by both listing ID (where applicable) and the authorized membership's organization ID. Public browsing intentionally exposes only active listings. All future tenant-owned resources must repeat the same authorization discipline.

## Not yet implemented
Inventory availability, product variants, images/uploads, search, categories, moderation, seller verification, carts, orders, shipping, payment processing, tax, returns, reviews, and marketplace disputes.
