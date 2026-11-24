# HIJAZ ONE Workforce Message Encryption

## Runtime contract

- The `workforce_service_messages.body` SQL column contains authenticated Fernet ciphertext with the `fernet:v1:` envelope. SQLAlchemy decrypts it only inside the application process.
- The relay API returns HTTP 503 if the current key is missing or invalid. It never falls back to plaintext storage or plaintext reads.
- Production startup requires a valid `WORKFORCE_MESSAGE_ENCRYPTION_KEY`. The key must be generated outside the repository and stored in the deployment secret manager.
- `WORKFORCE_MESSAGE_PREVIOUS_ENCRYPTION_KEYS` is a comma-separated list of previous Fernet keys for controlled rotation. Writes always use the current key; reads can authenticate with current or previous keys.
- Migration `0045_workforce_msg_encrypt` encrypts retained plaintext rows in place. If rows exist without a valid current key, migration stops before serving the application. It also expands the database body-length constraint to 16,384 characters for ciphertext while keeping the API plaintext cap at 2,000.
- Downgrade is intentionally blocked while encrypted messages exist. Do not remove old keys until an approved re-encryption and restore rehearsal has verified all retained records.
- Message sends use an atomic SQLite/PostgreSQL sliding-window limiter backed by the existing `auth_rate_limit_locks` and `auth_attempts` tables; no additional migration or rate-limit database is introduced. The default is 20 attempts per authenticated sender per 5 minutes, configurable with `WORKFORCE_MESSAGE_SEND_LIMIT` and `WORKFORCE_MESSAGE_SEND_WINDOW_MINUTES`. A throttled request returns HTTP 429 with `Retry-After`; the persisted subject is a SHA-256 digest of the authenticated user ID, not the raw ID or message body. Existing bounded security maintenance removes rate-limit records older than 24 hours.

## Key generation and handling

Generate a new key on a trusted offline/admin environment. Automated tests use ephemeral test-only keys; no production key is stored in the repository:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Set the resulting value as `WORKFORCE_MESSAGE_ENCRYPTION_KEY` in the runtime secret manager. Do not commit it, put it in application logs, include it in database backups, or share it in PR comments. Backups contain ciphertext; key escrow and recovery must be managed separately. Losing every valid key makes retained message bodies permanently unreadable.

For rotation, deploy the new key as current and place the old key(s) in `WORKFORCE_MESSAGE_PREVIOUS_ENCRYPTION_KEYS`. Verify reads and writes before any key retirement. This PR provides dual-key decryption, not an automatic bulk re-encryption/old-key retirement command; keep previous keys until a separately approved re-key operation and restore rehearsal complete.

## Security boundaries and open gates

This is authenticated encryption at rest, not end-to-end encryption. Authorized application code and participants still receive plaintext. TLS, role/ownership authorization, moderation/reporting, retention/deletion, attachment scanning, legal review and source-data migration remain separate controls. Database-backed per-sender abuse throttling is implemented; it is not a substitute for moderation/reporting or network-level denial-of-service protection. No external messaging provider is enabled and no Hatchable data or live configuration is changed.


## Participant reporting and platform moderation — Draft PR #77

- Either participant may report a message sent by the other participant on the same assigned service request. The client path is owner-scoped; provider paths require the exact active contractor, mason or labour entitlement and the assigned published profile.
- Reports use a fixed reason-code allowlist (spam, harassment, contact_sharing, scam_or_fraud, unsafe_content, other). There is no free-text report field, so reports do not create another channel for phone numbers, email addresses, URLs or identity documents.
- A participant cannot report their own message. A unique message/reporter constraint prevents duplicate reports by the same participant. Reports reference the encrypted message row; the report table never copies message text or contact data.
- Only a verified platform administrator configured in PLATFORM_ADMIN_EMAILS can read the moderation queue or review reports. The queue returns decrypted message context only to that administrator and omits participant user IDs and phone-verification fields.
- An administrator can dismiss a report or hide its message. Hidden messages are omitted from both participant message-list APIs. Hiding one message marks all open reports for that message as actioned. Review operations are transaction-bound and write allowlisted audit events containing status only, never message bodies or report text.
- This is a basic human moderation queue, not automated content classification, a complete appeals process, a legal adjudication workflow, or a retention/deletion policy. It does not scan attachments (attachments are not enabled), suspend accounts, refund fees, adjudicate service disputes, or enable external communications.
- The queue requires the workforce message encryption key and fails closed when encryption is not configured. No message body is copied into report rows or audit metadata.
