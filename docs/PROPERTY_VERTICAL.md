# HIJAZ ONE Property Vertical — Source Mapping and Portable Implementation

## Verified Hatchable Version 40 source

Read-only inspection of the live project on 2026-11-21 confirmed:

- Tables: `property_listings`, `property_transactions`, `property_payments`, and `user_phone_verifications`.
- Functions: `api/property-submit.js`, `api/property-marketplace.js`, `api/property-action.js`, and `api/property-admin.js`.
- The listing flow requires a phone number matching a user verification no older than 30 days.
- The deployed source confirms a PKR 500 listing fee and 1% buyer plus 1% seller commission.
- Source property rows include private addresses, seller phone, document keys, seller-declared dispute state, and title-verification state.

No Hatchable rows, identity documents, deeds, photos, credentials, or object bytes were exported or copied. The live Version 40 application remains unchanged.

## Source-to-port mapping

| Hatchable source | Portable target | Current status |
|---|---|---|
| `property_listings` | `PropertyListing` | Draft API and migration in this PR |
| `property_transactions` | `PropertyTransaction` | Draft bilateral interest/confirmation API |
| `property_payments` | `PropertyPayment` | Draft manual-review lifecycle |
| `user_phone_verifications` | `UserPhoneVerification` | Read-only verification gate; trusted writer/provider not yet implemented |

The portable schema uses UUIDs, PKR integer minor units, explicit state constraints, user ownership, bounded query results, and PostgreSQL/SQLite-compatible indexes. It does not import or transform source records.

## Implemented in this Draft slice

- Authenticated listing creation requires verified email and a provider-issued E.164 phone verification no older than 30 days. The phone number is taken from the verification record; the client cannot submit a different number or self-assert verification.
- Listing creation snapshots the confirmed PKR 500 fee as 50,000 minor units and creates a pending payment. A submitted payment reference remains `submitted`; it cannot mark the fee paid.
- Only a verified platform administrator can manually review submitted payment evidence. Publishing additionally requires a paid listing fee and explicit moderation evidence.
- Publication is deliberately separate from legal-title verification. A published listing remains `title_verification_status=not_verified`; seller dispute status is labelled as a seller declaration, not legal clearance.
- Public search returns published listings only and excludes private address, seller phone, payment reference, internal user IDs, and document keys. The owner can read their own private address.
- Buyer interest is unique per listing and buyer. The seller cannot express buyer interest in their own listing.
- Buyer and seller must confirm the same positive final sale price. Only after both confirmations are 1% buyer and 1% seller commissions calculated using integer PKR minor units and the existing half-up commission service.
- Commission payments remain pending until separately submitted and manually verified. A transaction moves to `commission_settled` only after both commission payments are verified; this does not certify the underlying property transfer or legal title.
- Audit events record allowlisted state and amount metadata only; payment references and private fields are not copied to audit details.

## Verification added

- SQLite API tests cover phone-verification freshness/future timestamps, email gating, payment-reference non-activation, admin-only review, moderation evidence, private-field redaction, title-status non-assertion, buyer/seller ownership, price mismatch, commission creation, and completion after both manual payment reviews.
- SQLite migration coverage asserts revision `0040_property_marketplace`, all four tables, and the partial unique payment indexes.
- A PostgreSQL-only integration test exercises listing fee review, publication, bilateral confirmation, both commission payments, and completion after the full Alembic upgrade.

## Explicit remaining gaps

- No portable OTP issuance/verification adapter, SMS credentials, rate-limit/replay service, or trusted phone-verification writer exists. Until one is implemented and configured, new portable listings fail closed unless a trusted verification record already exists.
- No property media upload, object-byte storage, deed/document workflow, title/legal verification, disputes/refunds, settlement, or FX conversion is implemented.
- The source's optional photos and document keys are intentionally not copied into the portable schema in this slice.
- No JazzCash, Easypaisa, bank, or other payment provider is connected. References are manual-review evidence only; this work does not move money.
- Full Hatchable export/import, redacted staging rehearsal, object checksums, relationship reconciliation, and rollback remain release gates. The 17-table source backup endpoint is incomplete and must not be used as the migration artifact.
