# Manual Bank Reconciliation MVP

## Scope
This workflow records a bank statement window and manually entered statement movements, then matches each movement to one posted journal line for the same tenant-owned active asset account. It does not connect to a bank, import CSV files, move money, or provide accounting advice.

## API
- `POST /api/v1/organizations/{organization_id}/reconciliations`: owner/admin opens a statement window for an active asset account, with statement start/end dates and opening/closing balances in integer minor units.
- `GET /api/v1/organizations/{organization_id}/reconciliations`: list sessions with bounded pagination.
- `GET /api/v1/organizations/{organization_id}/reconciliations/{session_id}/lines`: list imported statement lines.
- `POST /api/v1/organizations/{organization_id}/reconciliations/{session_id}/lines`: enter one statement movement; positive means a deposit/increase to the asset, negative means a withdrawal/decrease.
- `POST /api/v1/organizations/{organization_id}/reconciliations/{session_id}/lines/{line_id}/match`: match the statement movement to a journal line.
- `PUT /api/v1/organizations/{organization_id}/reconciliations/{session_id}/balances`: correct opening/closing balances while the session is open.
- `PUT /api/v1/organizations/{organization_id}/reconciliations/{session_id}/lines/{line_id}`: correct an unmatched statement line.
- `DELETE /api/v1/organizations/{organization_id}/reconciliations/{session_id}/lines/{line_id}`: remove an unmatched line with an audit event.
- `POST /api/v1/organizations/{organization_id}/reconciliations/{session_id}/lines/{line_id}/unmatch`: unmatch a line before correcting it.
- `POST /api/v1/organizations/{organization_id}/reconciliations/{session_id}/complete`: complete the session after all statement lines are matched and the statement arithmetic reconciles.

## Invariants
- Every route is owner/admin-only and scoped to the authorized organization.
- Statement windows for the same account must not overlap; application checks and PostgreSQL/SQLite triggers enforce this.
- Statement dates must fall within the session window.
- Matching requires a posted journal line from the same organization and account, dated within the window, whose `debit_minor - credit_minor` exactly equals the signed statement amount.
- A journal line can be matched only once across reconciliation sessions; a statement line can be matched only once.
- Duplicate non-empty external references within one session are rejected.
- A session cannot complete with no lines or unmatched lines. Opening balance plus all statement movements must equal the declared closing balance.
- Open sessions support audited balance corrections, unmatched-line edits/deletion, and explicit unmatching so an operator can recover from import mistakes. Matched lines must be unmatched before editing/deletion.
- Completed sessions cannot accept additional lines, edits, deletions, or new matches. Completion is idempotent.

## Known limitations
- Statement lines are entered individually; there is no file upload/parser or bank connector.
- Matching is exact one-to-one only. Split transactions, combined deposits, automatic suggestions, tolerances, fees, interest, and automatic adjusting journal entries are not supported.
- Completion validates statement arithmetic and that every imported statement line has a journal match. It does not require every journal line in the window to appear on the bank statement; timing differences remain the operator's responsibility.
- It is not payment reconciliation and does not mark orders paid, post settlements, or move funds.
- Production use requires independent accounting-domain review, controlled import provenance, backup/restore testing, and staging validation.
