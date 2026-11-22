# HIJAZ ONE Workforce Hiring — Version 40 Source Mapping

## Confirmed source workflow

Read-only Version 40 inspection confirmed the service_requests table and these fields: client/provider identifiers, service type, title/details, city/region/country, numeric budget, status, client phone snapshot and verification timestamp, client/provider acknowledgement booleans, payment method, PKR 50 platform fee and fee status.

The source marketplace-action function requires a provider-verified phone and client/provider acknowledgement booleans before inserting a request. The provider-workspace function returns assigned requests only after the source platform fee is paid and the provider identity status is verified. It supports bounded matched/accepted/in_progress/completed/cancelled/disputed transitions. The source uses JazzCash by default; the portable slice deliberately does not enable JazzCash or any external provider.

## Portable Draft implementation

- Adds workforce_service_requests and workforce_service_payments in Alembic revision 0043_workforce_service_requests.
- The client must have a verified email and a provider-issued E.164 phone verification no older than 30 days. Only the verification timestamp is snapshotted; no phone number is copied into the request or exposed in client/provider responses.
- Client terms are versioned and timestamped. Provider terms are separately accepted by the assigned provider when accepting a matched request; the client cannot self-assert provider acceptance.
- The source PKR 50 fee is represented as 5,000 PKR minor units. Payment methods are limited to manual bank transfer or cash deposit. A submitted reference remains submitted; only a verified platform administrator with evidence_checked=true can mark the fee verified.
- Admin assignment requires a verified fee, a published/available provider profile and a current active role entitlement.
- Contractor, mason and labour provider list/update endpoints each use require_active_role for the exact role. Provider visibility is limited to assigned requests with verified fees.
- State transitions are bounded and idempotent. Client cancellation is limited to new/matched work; accepted work must be disputed rather than silently cancelled.
- Audit events contain only allowlisted state, fee amount, currency and role scalars. Payment references and phone data are not written to audit events.
- No real payment/communication provider is enabled and no source rows were copied.

## Remaining gaps

This slice does not provide a messaging/contact relay, refunds, dispute adjudication, provider payout/settlement, multi-currency budgets, contractor-company teams, ratings, document storage, phone-provider integration, or source-data migration. The provider terms version is an acknowledgement marker, not a legally reviewed contract or electronic signature. The service fee is a confirmed source snapshot, not a country-wide configurable fee engine. No hiring request data was imported.
