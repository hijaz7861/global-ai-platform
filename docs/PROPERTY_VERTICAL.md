# HIJAZ ONE Property Vertical — Source Mapping and First Portable Slice

## Source evidence

Read-only inspection of the live Hatchable HIJAZ ONE Version 40 project on 2026-11-21 confirmed these property tables and API functions:

- Tables: `property_listings`, `property_transactions`, `property_payments`, and `user_phone_verifications`.
- API functions: `api/property-submit.js`, `api/property-marketplace.js`, `api/property-action.js`, and `api/property-admin.js`.
- The project schema contains no separate property-title registry or legal-verification evidence table.

No Hatchable rows, documents, photos, or storage objects were exported or copied.

## Confirmed source workflow

1. An authenticated seller submits a listing. The source checks that the submitted phone matches the user's phone verification and that the OTP verification is no older than 30 days.
2. The source snapshots a **PKR 500.00 listing fee**, creates a pending listing-fee payment record, and leaves the listing in `pending_fee`.
3. An administrator manually verifies or rejects the listing-fee payment. A verified listing fee moves the listing to `pending_review`; only then can an administrator publish or reject the listing.
4. Public search returns published listings only. Private address and document-storage keys must never be included in public responses.
5. A buyer can record interest. Buyer and seller independently confirm the same final sale price. Once both have confirmed, the source calculates **1% buyer commission and 1% seller commission**, creates two pending commission-payment records, and marks the listing sold.
6. An administrator manually verifies both commission payments. The transaction becomes completed only when both are verified.

The fee values above are confirmed by the deployed source code and public/admin API policy payloads; they are not inferred from UI labels.

## Required portable data mapping

| Hatchable source | Portable target concept | Required controls |
|---|---|---|
| `property_listings` | PropertyListing | seller ownership, tenant scope, moderation state, private-address separation, bounded media references |
| `property_transactions` | PropertyTransaction | buyer/seller identity, exact listing ownership, mutually confirmed sale price, idempotent state transitions |
| `property_payments` | PropertyPayment | role/type-specific amount snapshot, manual verification audit, no client-controlled verified state |
| `user_phone_verifications` | UserPhoneVerification | E.164 normalization, provider-issued OTP evidence, 30-day freshness, replay/rate limits |

## Important source caveat: moderation is not title verification

The Hatchable admin publication function sets `title_verification_status='verified_by_admin'` when an administrator publishes a listing after fee verification. The inspected workflow does not demonstrate an independent title/deed verification process. The portable model must therefore separate **listing moderation** from **legal-title verification** and must never translate seller declarations or ordinary publication into a claim that title has been verified.

The seller's `dispute_status` is a declaration (`seller_declared_non_disputed`, `disputed`, or `unknown`), not legal clearance. Optional latitude/longitude is a point location, not a road route or ETA.

## First portable implementation slice

- `app/services/property_commissions.py` implements the confirmed 1% + 1% commission calculation using integer PKR minor units and explicit half-up rounding.
- The confirmed PKR 500 listing fee and commission rates are named constants, not hidden UI literals.
- Regression and negative tests cover default rates, integer minor-unit arithmetic, half-up rounding, configurable basis-point rates, invalid prices, and invalid rates.
- The service does **not** create a payment, activate a provider, verify payment, settle funds, or publish a listing.

## Remaining blockers before property API activation

- The portable platform has not yet implemented the source's fresh phone-OTP verification workflow. No SMS provider credentials or owner setup are available; listing submission must remain blocked until a provider-neutral verification adapter and tests exist.
- Property listing/transaction/payment models, tenant and ownership APIs, admin moderation, manual payment review, media access controls, retention and audit integration remain to be implemented.
- Payment references remain review evidence only. No JazzCash or other external payment service is enabled by this work.
- Complete source-to-target mapping, object-byte export, staged import, checksums and reconciliation remain unperformed. The live Hatchable project is untouched.
