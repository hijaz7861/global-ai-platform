# HIJAZ ONE Workforce Directory — Version 40 Source Mapping

## Verified Hatchable source inventory

Read-only inspection of the active HIJAZ ONE Hatchable Version 40 project confirmed:

- Source table: workforce_profiles.
- Fee configuration table: workforce_registration_fee_settings.
- Relevant API functions: api/marketplace-action.js, api/marketplace.js, api/marketplace-admin.js and api/provider-workspace.js.
- The source profile stores full name, trade, skills, numeric experience, phone, city, region, country, service radius, biography, availability, travel preference, available cities, GPS coordinates, fee state and age/identity review fields.
- Source registration fees are PKR 500 for contractors, PKR 200 for masons and PKR 50 for labour. The portable role catalogue already represents these as PKR minor units: 50,000; 20,000; and 5,000 respectively. This vertical does not create a second fee authority.

No workforce rows, phone numbers, GPS coordinates, identity evidence, payment references, credentials or media were exported. The live Hatchable application was not changed.

## Portable implementation in this Draft slice

- Adds workforce_profiles with one independent profile per user and professional role: contractor, mason or labour.
- Each write endpoint uses require_active_role for its exact role. The existing role entitlement remains authoritative for verified email, age review, fee/payment review, expiry, suspension and revocation.
- Profile updates reset moderation to pending. A verified platform administrator must review content before publication.
- Public discovery and profile detail re-check the current global role entitlement, role enablement, account/email state, age verification, fee/payment state and expiry on every read. Suspended, expired, revoked, disabled or unpaid roles are not discoverable.
- Public responses omit user IDs, email, phone, GPS coordinates, age/identity references, payment references and reviewer notes.
- No user-submitted adult declaration, payment reference or moderation status is accepted. Age and fee proof are not duplicated in the workforce profile.
- Country uses a two-letter uppercase code; city, region and service radius are descriptive filters only. GPS and road ETA are not implemented.
- Skills and available cities are bounded JSON lists. Experience is numeric with a 0–70 year database constraint.
- SQLite and PostgreSQL use the same Alembic migration, named constraints and indexes. PostgreSQL integration covers JSON round-trip and user/role uniqueness.

## Source-to-port status

| Hatchable source | Portable target | Status |
|---|---|---|
| workforce_profiles.full_name | WorkforceProfile.full_name | Draft |
| workforce_profiles.trade | role_key plus specialty | Draft; explicit role keys replace ambiguous free-text fee grouping |
| workforce_profiles.skills | WorkforceProfile.skills | Draft; normalized JSON list |
| workforce_profiles.experience_years | WorkforceProfile.experience_years | Draft; bounded numeric |
| workforce_profiles.city/state_region/country | city/region/country_code | Draft |
| workforce_profiles.service_radius_km | service_radius_km | Draft |
| workforce_profiles.travels_for_work/available_cities | travels_for_work/available_cities | Draft |
| workforce_profiles.bio/availability | bio/availability | Draft |
| workforce_profiles.phone/latitude/longitude | — | Not ported; private contact and precise location are not exposed or stored |
| workforce_profiles.age/fee/identity fields | central role entitlements | Replaced by verified entitlement lifecycle |
| workforce registration fee settings | role_definitions | Reused; no duplicate fee table |
| admin worker review | workforce profile moderation | Draft; distinct from age/identity verification |
| service_requests hiring workflow | — | Not yet ported |

## Remaining gaps

This slice does not implement hiring requests, contractor-company membership, workforce ratings/reviews, phone verification, map routing, notifications, payment providers, identity-document storage or source-data migration. It does not claim legal identity verification merely because a role entitlement is active; that entitlement reflects an authorized manual review in the current implementation. No Hatchable data or files were imported.

Keep this PR Draft. It does not merge, deploy, alter Hatchable, enable providers, or declare the full HIJAZ ONE Definition of Done satisfied.
