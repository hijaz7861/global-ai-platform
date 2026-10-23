# Double-Entry Ledger MVP

## API
- `POST /api/v1/organizations/{organization_id}/accounts`: owner/admin creates an account.
- `GET /api/v1/organizations/{organization_id}/accounts`: member-scoped account list.
- `POST /api/v1/organizations/{organization_id}/journal-entries`: owner/admin posts a balanced journal entry. Requires `Idempotency-Key` (8–128 characters).
- `GET /api/v1/organizations/{organization_id}/journal-entries`: owner/admin lists posted entries.

## Invariants
- Each journal line has either a positive debit or a positive credit, never both or neither.
- Every entry has at least two lines and total debits must equal total credits.
- Each entry uses active accounts belonging to the same organization and the same currency.
- Amounts are integer minor units; binary floating-point values are not used.
- A per-organization idempotency key and request fingerprint prevent duplicate posting and reject key reuse with a different payload.
- Journal entries are posted on creation and there is no update/delete API. Corrections should use explicit reversing entries; reversal workflows are not implemented yet.
- Posting and its audit event share one database transaction.

## Limits
This is a ledger foundation, not a complete accounting system. It does not calculate account balances or financial statements, provide a default chart of accounts, handle period closing, tax, multi-currency exchange, payment reconciliation, supplier settlement, approval workflows, or reversals. No payment provider is connected and no external money is moved.

Before production, add financial-domain review, reconciliation and reversal workflows, immutable/tamper-evident audit export, period controls, PostgreSQL concurrency tests, and independent accounting validation.
