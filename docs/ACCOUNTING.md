# Double-Entry Ledger MVP

## API
- `POST /api/v1/organizations/{organization_id}/accounts`: owner/admin creates an account.
- `GET /api/v1/organizations/{organization_id}/accounts`: member-scoped account list.
- `POST /api/v1/organizations/{organization_id}/journal-entries`: owner/admin posts a balanced journal entry. Requires `Idempotency-Key` (8–128 characters). Once an organization configures periods, new postings and reversals require an open period covering the current UTC date; a matching idempotent replay remains available after closure.
- `POST /api/v1/organizations/{organization_id}/accounting-periods`: create a non-overlapping date range; PostgreSQL and SQLite triggers reject overlapping inserts and updates at the database layer.
- `GET /api/v1/organizations/{organization_id}/accounting-periods`: list periods.
- `POST /api/v1/organizations/{organization_id}/accounting-periods/{period_id}/close`: close a period; closure is idempotent and there is no reopen endpoint.
- `GET /api/v1/organizations/{organization_id}/journal-entries`: owner/admin lists posted entries.
- `GET /api/v1/organizations/{organization_id}/trial-balance`: derives debit totals, credit totals, and normal balances from posted journal lines.

## Invariants
- Each journal line has either a positive debit or a positive credit, never both or neither.
- Every entry has at least two lines and total debits must equal total credits.
- Each entry uses active accounts belonging to the same organization and the same currency.
- Amounts are integer minor units; binary floating-point values are not used.
- A per-organization idempotency key and request fingerprint prevent duplicate posting and reject key reuse with a different payload.
- Journal entries are posted on creation and there is no update/delete API. Corrections use explicit immutable reversing entries, with at most one reversal per original entry.
- Posting and its audit event share one database transaction.

## Limits
This is a ledger foundation, not a complete accounting system. Period controls are enforced after an organization creates at least one period; organizations with no configured periods retain legacy posting behavior. The current guard uses the server UTC date and does not yet store a distinct accounting date on each journal entry. It does not provide full financial statements, a default chart of accounts, tax, multi-currency exchange, payment reconciliation, supplier settlement, approval workflows, or independent accounting validation. No payment provider is connected and no external money is moved.

Audit events are protected against UPDATE/DELETE by database triggers in PostgreSQL and SQLite, but this is not a cryptographic hash chain and does not protect against a privileged operator disabling triggers. Before production, add financial-domain review, reconciliation, tamper-evident external audit export, independent accounting validation. Database triggers and PostgreSQL concurrency tests now enforce period non-overlap; financial reconciliation and independent accounting validation remain open.
