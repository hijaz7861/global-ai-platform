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
- `POST /api/v1/me/role-entitlements`: apply for a role; the server snapshots the configured fee and currency.
- `GET /api/v1/me/role-entitlements`: list only the authenticated user's applications.
- `PATCH /api/v1/platform/role-definitions/{role_key}`: platform-admin-only fee/currency/minimum-age/availability configuration.
- `POST /api/v1/platform/role-entitlements/{id}/review`: platform-admin-only approve/reject; activation requires explicit payment verification for paid roles and explicit age verification where required.

A paid role starts in `pending_payment`; a free role starts in `pending_review`. A user-supplied payment reference is evidence for manual review only and never marks a payment as verified. Only the platform administrator review action can mark the fee verified and activate the entitlement. The approval action also requires the administrator to affirm that age/identity evidence has been checked for adult roles.

## Server-side authorization

Use `require_active_role("role_key")` as a FastAPI dependency on every role-specific route. It rejects missing, pending, rejected, expired, suspended, and revoked entitlements. Organization membership checks remain separate and must also be applied where a role action is organization-scoped.

The current migration establishes the entitlement system and reusable authorization dependency. Existing supplier, transport, property, workforce, education and construction endpoints still need to be migrated to this dependency before the corresponding vertical can be declared protected by the central role system.

## Security limitations and next work

- Payment verification is currently an explicit administrator/manual control, not a payment gateway.
- Age verification is an administrator attestation; no identity-document or biometric verification provider is integrated.
- Role definitions are currently a platform-level catalogue. Country-specific fee policies and organization-scoped role entitlements need an additional policy layer before multi-country commercial launch.
- Existing Hatchable profiles are not automatically imported or activated. A reviewed mapping and user/organization reconciliation is required.
- Role-entitlement events are append-only by application convention; production database permissions/trigger hardening should be added to the audit hardening phase.
