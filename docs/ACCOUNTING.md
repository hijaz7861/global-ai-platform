# Double-Entry Ledger and Reconciliation Foundation

## Accounting API
- `POST /api/v1/organizations/{organization_id}/accounts`: owner/admin creates an account.
- `GET /api/v1/organizations/{organization_id}/accounts`: tenant-scoped account list.
- `POST /api/v1/organizations/{organization_id}/journal-entries`: owner/admin posts a balanced entry; requires `Idempotency-Key`.
- `POST /api/v1/organizations/{organization_id}/journal-entries/{entry_id}/reverse`: creates one immutable counter-entry.
- `POST /api/v1/organizations/{organization_id}/accounting-periods`: create a non-overlapping period; PostgreSQL/SQLite triggers reject overlapping periods.
- `GET /api/v1/organizations/{organization_id}/accounting-periods`: list periods.
- `POST /api/v1/organizations/{organization_id}/accounting-periods/{period_id}/close`: audited one-way closure.
- `GET /api/v1/organizations/{organization_id}/trial-balance`: derive debit, credit and normal balances.
- `GET /api/v1/organizations/{organization_id}/accounts/{account_id}/ledger`: bounded cursor-paginated ledger with opening/running balances.
- `GET /api/v1/organizations/{organization_id}/income-statement`: date-filtered revenue, expense and net income grouped by currency.
- `GET /api/v1/organizations/{organization_id}/balance-sheet`: derived balances grouped by currency.

## Reconciliation API
The manual reconciliation workflow is documented in [Bank Reconciliation](RECONCILIATION.md). It allows owner/admin users to open a statement window for an active asset account, enter signed statement movements, match each movement to one posted journal line for the same account and date window, and complete the session after balance arithmetic and matching checks pass.

## Ledger invariants
- Every journal line has either a positive debit or positive credit, never both or neither.
- Each entry has at least two lines and total debits equal total credits.
- Each entry uses active accounts in the same organization and currency.
- Amounts use integer minor units; binary floating-point is not used.
- A per-organization idempotency key and request fingerprint prevent duplicate posting and reject key reuse with a different payload.
- Journal entries are immutable after posting; corrections use explicit reversing entries, with at most one reversal per original.
- Posting and its audit event share one database transaction.
- Once accounting periods are configured, new postings and reversals require an open period covering the current UTC date. The entry model currently uses its creation timestamp rather than a separately supplied accounting date.

## Limitations
This is not a complete accounting system. Financial reconciliation is a manual one-statement-line-to-one-journal-line MVP; it has no bank API/CSV parser, automatic matching suggestions, split/combined matching, bank fees automation, payment reconciliation, cash-flow statement, tax, foreign exchange, supplier settlement, closing workflow, or independent accounting validation. The balance sheet reports accumulated net income as current earnings until a closing workflow exists. No payment provider is connected and no external money is moved.

Audit events have database-level UPDATE/DELETE protection in PostgreSQL and SQLite, but this is not a cryptographic hash chain and does not protect against a privileged operator disabling triggers. Before production, obtain financial-domain review, reconciliation validation, tamper-evident external audit export, and staging evidence.
