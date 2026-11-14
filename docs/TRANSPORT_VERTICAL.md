# HIJAZ ONE Transport Vertical — Portable Slice

## Live source inventory

The existing application remains live at https://hijaz-construction.hatchable.site. This run used read-only project, schema, file, function, and source reads only. No source rows, media, credentials, documents, backups, live files, tables, functions, settings, or deployments were changed.

Hatchable Version 40 confirms:
- `transport_profiles`: one profile per Hatchable user, business/contact/location fields, registration-fee state, identity/age review state, timestamps.
- `transport_vehicles`: profile/user ownership, type/title/model, registration number, capacity/operator details, GPS/location, pricing basis and PKR numeric rates, service radius, availability, verification/document states.
- `transport_bookings`: client/provider references, verified-phone timestamp, pickup/drop-off, booking time/load, quoted rate, platform fee/payment state, terms acknowledgements, booking lifecycle.
- `transport_registration_fee_settings`, migrations `0013_transport_and_machinery_rental.sql` and `0014_transport_verification_reference.sql`, API functions `api/transport-action.js` and `api/transport-marketplace.js`.

## Portable mapping implemented

| Hatchable source | Portable target | Mapping |
|---|---|---|
| `transport_profiles` | `transport_provider_profiles` | One profile per organization, business name, city/region, country code, service description, moderation state. |
| `transport_vehicles` | `transport_vehicles` | Organization-scoped vehicle listing, type/title/model/capacity, operator-included, city/region/country, pricing basis, integer minor-unit rate, currency, minimum charge, service radius, availability, verification. |
| `transport_registration_fee_settings` | `role_definitions.transporter` | Central role fee configured by verified platform admin; null fee fails closed. |
| `transport_bookings` | Not implemented | Needs portable verified-phone OTP, idempotent booking, payment intent/fee policy, notifications, cancellation/disputes, settlement/reconciliation. |

The source profile is user-scoped; the portable profile is organization-scoped to match multi-role entitlements. Public discovery checks active/unexpired role, age/payment state, verified email, active account, current membership, approved provider profile, verified vehicle, and available status.

## Exclusions and limitations

No phone numbers, operator contacts, vehicle registration numbers, document references, identity documents, deeds, uploaded media, GPS coordinates, bookings, OTP state, booking fees, or payment references are stored or copied. No source-row export or migration has occurred. Country/currency fields enforce uppercase two-/three-letter syntax only; ISO registry membership and currency exponent metadata remain release work. Straight-line GPS is not road distance or ETA.

## Security boundary

Organization endpoints require membership plus `require_active_role("transporter", organization_scoped=True)`. Profile and vehicle moderation is separate and requires a verified platform administrator's manual evidence attestation. Public discovery only returns approved providers and verified/available vehicles. Material vehicle edits reset verification to pending; suspended/expired roles hide vehicles. No booking, payment gateway, phone OTP, WhatsApp/SMS, or logistics provider is enabled.

This is a portable listing slice, not a source-data migration or complete transport vertical.
