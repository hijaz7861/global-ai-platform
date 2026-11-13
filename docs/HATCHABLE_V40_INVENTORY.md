# HIJAZ ONE — Hatchable Version 40 Read-Only Inventory

**Source application:** [hijaz-construction.hatchable.site](https://hijaz-construction.hatchable.site)
**Inspected version:** 40
**Deployment state:** live
**Deployment timestamp returned by Hatchable:** 2026-11-13T07:25:10Z
**Inspection mode:** metadata and source-file reads only. No table rows, credentials, private documents, backups or media objects were exported. No live project, file, schema, function, deployment or setting was changed.

## 1. Verified inventory totals

| Resource | Verified count |
|---|---:|
| Database tables | 46 |
| Project files | 75 |
| API functions | 25 |
| SQL migrations | 14 (`0001`–`0014`) |
| Current live version | 40 |

## 2. Table inventory by business domain

### Construction, quoting and service operations
- `clients`, `projects`, `transactions`, `labour_entries`, `material_entries`, `pricing_items`, `quotes`, `service_requests`.

### Suppliers and procurement
- `suppliers`, `supplier_products`, `quote_requests`, `material_orders`.

### Property
- `property_listings`, `property_payments`, `property_transactions`.

### Transport and machinery
- `transport_profiles`, `transport_vehicles`, `transport_bookings`, `transport_registration_fee_settings`.

### Workforce and registration
- `workforce_profiles`, `workforce_registration_fee_settings`, `registration_fee_payments`.

### Education
- `academies`, `teacher_profiles`, `courses`, `course_materials`, `course_enrollments`, `student_profiles`, `platform_education_settings`.

### Customer engagement and consent
- `engagement_conversations`, `engagement_chat_messages`, `engagement_leads`, `engagement_messages`, `engagement_agent_events`, `engagement_unsubscribe_tokens`.

### Platform, identity and governance
- `users`, `user_memberships`, `membership_plans`, `platform_fee_settings`, `platform_fee_payments`, `platform_reviews`, `platform_media`, `platform_otp_attempts`, `user_phone_verifications`, `audit_events`, `assistant_runtime_settings`.

## 3. API function inventory

The Hatchable function registry returned 25 functions. The corresponding source files are:

- `api/assistant.js`, `api/assistant-admin.js`
- `api/backup.js`
- `api/education.js`, `api/education-admin.js`
- `api/engagement.js`, `api/engagement-admin.js`, `api/jobs/follow-up.js`
- `api/management.js`
- `api/marketplace.js`, `api/marketplace-action.js`, `api/marketplace-admin.js`
- `api/media-upload.js`, `api/phone-otp.js`
- `api/property-action.js`, `api/property-admin.js`, `api/property-marketplace.js`, `api/property-submit.js`
- `api/provider-workspace.js`
- `api/supplier-account.js`, `api/supplier-admin.js`, `api/suppliers.js`
- `api/transport-action.js`, `api/transport-marketplace.js`
- `api/unsubscribe.js`.

Function metadata reports 25 functions; HTTP methods and access policy are declared in each source file and must be mapped endpoint-by-endpoint before porting.

## 4. Migration inventory

Fourteen SQL migrations were present:

- `0001_construction_management.sql`
- `0002_quotes_and_pricing.sql`
- `0003_enterprise_quote_controls_and_audit.sql`
- `0004_supplier_marketplace.sql`
- `0005_verified_marketplace_and_media.sql`
- `0006_mobile_first_categories_and_travel.sql`
- `0007_phone_otp_orders_and_payments.sql`
- `0008_real_estate_marketplace.sql`
- `0009_customer_engagement_automation.sql`
- `0010_membership_payment_verification.sql`
- `0011_workforce_education_ai_control.sql`
- `0012_education_age_and_course_materials.sql`
- `0013_transport_and_machinery_rental.sql`
- `0014_transport_verification_reference.sql`.

## 5. File inventory

### Root and configuration
- `AGENTS.md`, `hatchable.toml`.

### API source
- `api/assistant-admin.js`, `api/assistant.js`, `api/backup.js`, `api/education-admin.js`, `api/education.js`, `api/engagement-admin.js`, `api/engagement.js`, `api/jobs/follow-up.js`, `api/management.js`, `api/marketplace-action.js`, `api/marketplace-admin.js`, `api/marketplace.js`, `api/media-upload.js`, `api/phone-otp.js`, `api/property-action.js`, `api/property-admin.js`, `api/property-marketplace.js`, `api/property-submit.js`, `api/provider-workspace.js`, `api/supplier-account.js`, `api/supplier-admin.js`, `api/suppliers.js`, `api/transport-action.js`, `api/transport-marketplace.js`, `api/unsubscribe.js`.

### Public web assets
- `public/ai-control-center.html`, `public/assistant.html`, `public/control-center.html`, `public/dashboard.css`, `public/dashboard.html`, `public/dashboard.js`, `public/education-admin.html`, `public/education.html`, `public/engagement-admin.html`, `public/engagement.html`, `public/estimator.css`, `public/estimator.html`, `public/estimator.js`, `public/hijaz-one-mark.svg`, `public/index.html`, `public/join-workforce.html`, `public/login.html`, `public/marketplace-order.js`, `public/marketplace.html`, `public/my-requests.html`, `public/one-home.css`, `public/properties.html`, `public/property-admin.html`, `public/provider-workspace.html`, `public/quote-request.html`, `public/quote-request.js`, `public/site.js`, `public/supplier-admin.html`, `public/suppliers.html`, `public/theme.css`, `public/transport-admin.html`, `public/transport.html`, `public/unsubscribe.html`, `public/work.html`.

## 6. Backup/export gap — do not use as migration

Source inspection of `api/backup.js` shows an admin-only GET endpoint. It exports JSON `schema_version: 1` and queries only these 17 tables:

- `clients`, `projects`, `transactions`, `labour_entries`, `material_entries`, `pricing_items`, `quotes`, `audit_events`, `suppliers`, `supplier_products`, `quote_requests`, `workforce_profiles`, `service_requests`, `material_orders`, `platform_reviews`, `platform_media`, `platform_fee_settings`.

That is 17/46 tables (29 omitted). The function inventory contains no import endpoint. The media export includes `storage_key` and metadata, not the underlying object bytes. Therefore this endpoint is not a complete, tested migration backup and must not be treated as a portable restore artifact.

## 7. Sensitive data and reconciliation constraints

The schema contains personal, financial, identity, consent and location fields. Examples include:

- `users.email`, `users.google_sub`, `users.hatchable_user_id` and `user_phone_verifications`.
- `property_listings.address_private`, `document_keys`, seller declarations, dispute and title-verification statuses.
- `transport_vehicles.registration_number`, document references and GPS coordinates.
- payment references and verification statuses in `registration_fee_payments`, `platform_fee_payments`, `property_payments`, `course_enrollments` and other order tables.
- engagement consent flags, unsubscribe-token hashes, follow-up state and message content.
- `platform_media.storage_key` and media metadata.

Do not copy raw identity documents, deeds, private addresses, phone numbers, tokens or message content into a development environment. A future staging export must use explicit owner-approved scope, least-privilege access, redaction/minimization, object-storage checksums and a documented retention window.

Migration mapping must explicitly reconcile:

1. Hatchable user IDs and external identity subjects to portable user IDs; do not assume email equality alone proves identity.
2. Organization, client, supplier, transporter, property-agent, teacher, student and workforce ownership relationships.
3. Status vocabularies and verification evidence, including age, phone, supplier, vehicle, property-title, payment and moderation states.
4. Money stored as PKR numeric fields into decimal-safe integer minor units with currency and source-value audit.
5. Consent and opt-out history without silently re-subscribing anyone.
6. Media object bytes, content types, sizes, hashes and storage keys.
7. Audit history and foreign-key ordering, with source counts and target counts reconciled per table.

## 8. Safe next steps

1. Obtain owner approval for a staging-only export scope and privacy/redaction policy.
2. Build a complete export manifest covering all 46 tables and all referenced object-storage assets; do not rely on the current 17-table endpoint.
3. Build an idempotent import path with explicit schema versioning, FK ordering, conflict reports and no destructive overwrite by default.
4. Rehearse export/import against an isolated PostgreSQL staging database; compare row counts, checksums, relationships, financial totals, consent states and audit-event totals.
5. Test rollback and restore from the resulting backup before proposing any production cutover.

**Current state:** inventory verified; no source data copied; no import/export rehearsal performed; live Hatchable Version 40 remains unchanged.
