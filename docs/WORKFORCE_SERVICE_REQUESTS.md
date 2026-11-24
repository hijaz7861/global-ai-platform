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

This slice does not provide refunds, dispute adjudication, provider payout/settlement, multi-currency budgets, contractor-company teams, ratings, document storage, phone-provider integration, or source-data migration. The provider terms version is an acknowledgement marker, not a legally reviewed contract or electronic signature. The service fee is a confirmed source snapshot, not a country-wide configurable fee engine. No hiring request data was imported.


## Internal contact relay — Draft PR #74

The next portable slice adds participant-only internal messages for an assigned workforce request:

- Client access is limited to the request owner; provider access is limited to the exact assigned, published contractor/mason/labour profile and the matching active role entitlement.
- A verified fee and assigned provider are required before either side can open the thread.
- Clients can message while work is matched/accepted/in progress. Providers can message only after accepting the provider terms, while work is accepted/in progress. Existing messages remain readable to participants after the work reaches a terminal state.
- Message bodies are limited to 2,000 characters. The API rejects recognizable phone numbers, email addresses, URLs and @handles; this is a narrow input guard, not comprehensive DLP.
- The table stores only request ID, sender user ID, sender side, body and timestamp. API responses omit sender IDs and contact verification data. Audit records identify the message event and contain only sender side and body length; message content is not copied to the audit log.
- No SMS, WhatsApp, email, push notification, phone-number disclosure, attachment upload or external provider is enabled.

The Draft implementation stores message bodies as application data; end-to-end encryption and at-rest encryption configuration are not established by this slice. It does not yet provide message retention/deletion controls, moderation/reporting, abuse throttling, attachment scanning, delivery/read receipts, legal terms review or production data import. Do not treat the contact relay as a substitute for a secure external communications provider.



## Workforce message encryption — Draft PR #75

Message bodies are now encrypted before SQLAlchemy writes them to `workforce_service_messages.body` using Fernet authenticated encryption. The database stores the `fernet:v1:` ciphertext envelope; application responses decrypt only after the participant authorization checks. The API returns HTTP 503 and does not write or read relay content when the current key is absent or malformed. Production startup additionally requires a valid `WORKFORCE_MESSAGE_ENCRYPTION_KEY`.

Migration `0045_workforce_msg_encrypt` encrypts any pre-existing plaintext rows before the application serves them. If rows exist and the current key is absent/invalid, the migration fails closed. The database check constraint is raised to 16,384 characters because a 2,000-character plaintext message expands when encrypted; the API input limit remains 2,000 characters. Downgrade is blocked while encrypted message rows exist to avoid silently making them unreadable.

Keys are deployment secrets and must be stored separately from the database and its backups. Generate a key offline with:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Set `WORKFORCE_MESSAGE_ENCRYPTION_KEY` in the runtime secret manager; never commit it or place it in a database backup. During key rotation, configure the new key as current and comma-separated old Fernet keys in `WORKFORCE_MESSAGE_PREVIOUS_ENCRYPTION_KEYS`. New writes use the current key and reads accept current/previous keys. Do not remove an old key until an approved re-encryption/retirement procedure has verified every retained row. Automated key retirement is not claimed by this PR.

This is encryption at rest, not end-to-end encryption: the application process and authorized participants can access plaintext. Transport TLS, retention/deletion policy, moderation/reporting, abuse throttling, legal review and production data migration remain separate release gates. No Hatchable data was exported and the live application was not changed.


## Participant message reporting — Draft PR #77

The dependent workforce messaging stack adds a participant report workflow:

- A client can report only a provider-authored message in that client's own assigned request. A contractor, mason or labour provider can report only a client-authored message for a request assigned to that exact published profile and active role.
- Reports use a fixed reason-code allowlist and do not accept free-text descriptions. One participant can create at most one report per message.
- A verified platform administrator can inspect the report queue and encrypted message context, then dismiss the report or hide the message. Hiding removes the message from both participant read APIs and marks all open reports for that message actioned.
- Report rows contain references, reason/status and review timestamps only; they do not duplicate message bodies, phone numbers or email addresses. Audit records store status only.
- This is a first moderation control. Appeals, account sanctions, retention/deletion policy, legal review, attachment scanning and formal service-dispute adjudication remain release gates.
