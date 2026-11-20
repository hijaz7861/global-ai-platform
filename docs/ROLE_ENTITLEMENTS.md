# HIJAZ ONE Role Entitlements

## Purpose

A single HIJAZ ONE account can apply for multiple independent platform roles. A role application never grants access to another role, and an active role is not inferred from a profile, URL, payment reference, or client-side flag.

## Initial role catalogue

The initial migration seeds:
- Student: free, but still subject to platform review; course purchases are separate.
- Teacher: PKR 200.00, minimum age 18, payment and identity/age review.
- Contractor: PKR 500.00, minimum age 18, payment and identity/age review.
- Mason: PKR 200.00, minimum age 18, payment and identity/age review.
- Labour: PKR 50.00, minimum age 18, payment and identity/age review.
- Supplier, transporter, property agent, construction provider: disabled for applications until an administrator configures their fee (the seed uses a null fee to fail closed).

Amounts are stored as integer minor units (for PKR, paisa). These are initial Pakistan-specific values inherited from the existing HIJAZ ONE business rules, not a global pricing policy. The platform administrator must configure a fee and ISO 4217 currency for each country/market before opening that market.

## API lifecycle

- `GET /api/v1/role-definitions`: list active role definitions and whether the fee is configured.
- `POST /api/v1/me/role-entitlements`: apply for a role; the server snapshots the configured fee and currency. A verified email is mandatory even when general email-verification enforcement is disabled.
- `GET /api/v1/me/role-entitlements`: list only the authenticated user's applications.
- `PATCH /api/v1/platform/role-definitions/{role_key}`: platform-admin-only fee/currency/minimum-age/availability configuration. Send `{"clear_fee": true}` to explicitly remove a configured fee; new applications then fail closed until a fee is configured again. Every changed field, including fee creation/change/clearing, is recorded in the append-only platform audit log using allowlisted scalar before/after fields.
- `GET /api/v1/platform/role-entitlements`: verified platform-admin-only, bounded review queue with status/role filters and an internal applicant ID; it does not return applicant email or identity documents.\n- `POST /api/v1/platform/role-entitlements/{id}/review`: platform-admin-only approve/reject; activation requires explicit payment verification for paid roles and explicit age verification where required.
- `POST /api/v1/platform/role-entitlements/{id}/lifecycle`: platform-admin-only suspend, reinstate, or permanently revoke an entitlement. Reinstatement applies the same hard minimum age floor as initial approval, including the 18-year professional-role floor even if the role catalogue is changed outside the API.

`run_maintenance_once` also performs a bounded role-expiry sweep: due `active` and `suspended` entitlements transition to `expired`, with an append-only role event and platform audit event written in the same transaction. The operation is idempotent and expired rows no longer occupy the pending/active uniqueness slot, so users can reapply. Production must schedule `python -m app.maintenance` with an external cron/CronJob; no hosted scheduler is assumed. SQLite maintenance tests and the PostgreSQL integration suite verify active/suspended expiry, audit-event insertion, and idempotent repeated sweeps.

A paid role starts in `pending_payment`; a free role starts in `pending_review`. A user-supplied payment reference is evidence for manual review only and never marks a payment as verified. Only the platform administrator review action can mark the fee verified and activate the entitlement. The approval action rechecks that the applicant account is still active, the email is still verified, and the role definition is still active. It also requires the administrator to affirm that age/identity evidence has been checked for adult roles. Professional roles (`teacher`, `contractor`, `mason`, `labour`, `supplier`, `transporter`, `property_agent`, `construction_provider`) have a hard minimum age floor of 18: administrator configuration cannot lower that floor, and the runtime authorization dependency enforces it even if a database value is changed outside the API. Age remains a manual administrator attestation; no identity-document provider is integrated.

Rejected, expired, and revoked applications no longer occupy the active/pending uniqueness slot, so a user may submit a new application after a terminal decision. Pending, active, and suspended applications remain unique per user/role/scope. Entitlement event records are not cascade-deleted with the entitlement. Migration 0035 installs SQLite and PostgreSQL triggers that reject UPDATE and DELETE on the event table; actor IDs are stored as audit snapshots rather than foreign keys. Production database roles must still prevent privileged trigger removal or bypass.

## Server-side authorization

Use `require_active_role("role_key")` as a FastAPI dependency on every role-specific route. It requires a verified email, active role definition, active unexpired entitlement, verified payment for paid roles, and age verification for adult roles. `organization_scoped=True` requires an entitlement for the exact `organization_id` path scope; it never silently substitutes a role from another organization. Organization membership and resource ownership checks remain separate and must also be applied.

The supplier procurement workflow now requires an active organization-scoped supplier entitlement for quote submission, revision, withdrawal, and purchase-order fulfilment. Supplier profile creation/submission and platform review remain available as the onboarding path before activation.

The current migration establishes the entitlement system and reusable authorization dependency. Existing transport, property, workforce, education and construction endpoints still need to be migrated to this dependency before those verticals can be declared protected by the central role system.

## Security limitations and next work

- Payment verification is currently an explicit administrator/manual control, not a payment gateway.
- Age verification is an administrator attestation; no identity-document or biometric verification provider is integrated.
- Role definitions are currently a platform-level catalogue. Country-specific fee policies and organization-scoped role entitlements need an additional policy layer before multi-country commercial launch.
- Existing Hatchable profiles are not automatically imported or activated. A reviewed mapping and user/organization reconciliation is required.
- Database triggers reject application-level UPDATE/DELETE of entitlement event rows on SQLite and PostgreSQL. Production database roles must still prevent privileged trigger removal or bypass.


## Concurrent administration safety — 2026-11-18

Role application, role-definition updates, administrator review, and entitlement
lifecycle transitions now acquire PostgreSQL row locks for the affected role
definition and/or entitlement before validating state and writing changes.
This serializes concurrent fee/catalogue edits with application/approval and
prevents two administrators from independently reviewing or transitioning the
same entitlement from the same stale state.

The focused regression test compiles the lock queries with the PostgreSQL
dialect and asserts that they include `FOR UPDATE`. SQLite remains useful for
local functional tests, but it does not provide PostgreSQL row-lock semantics;
the exact-head PostgreSQL integration workflow remains required before this
change is considered verified.

## Current integration status — 2026-11-20

The role-entitlement implementation is being reconciled onto a fresh
integration commit whose first parent is the current `master` head and whose
second parent preserves the original role-entitlement branch history. The
current master AI audit controls and memory-retention maintenance behavior are
retained. PostgreSQL regression coverage now includes concurrent uniqueness
for both global and organization-scoped applications.

This remains a Draft review candidate. Exact-head CI, empty-database and
upgrade-path migration checks, owner review, and endpoint-by-endpoint
authorization coverage remain required. No production data, live Hatchable
application, credentials, or external provider was touched.
