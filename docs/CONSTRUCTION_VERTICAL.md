# HIJAZ ONE Construction Vertical — Portable Project and BOQ Foundation

**Status:** Project/BOQ, quote and cashbook slices exist in Draft PRs #26–#28 and are being refreshed together onto the current-master integration base. None is merged or deployed.

## Source inventory and evidence

Read-only inspection of the live Hatchable Version 40 project confirmed 46 database tables, 75 files, 25 active API functions, and 14 SQL migrations. Construction source evidence includes \`clients\`, \`projects\`, \`transactions\`, \`labour_entries\`, \`material_entries\`, \`pricing_items\`, and \`quotes\`; migrations \`0001_construction_management.sql\`, \`0002_quotes_and_pricing.sql\`, \`0003_enterprise_quote_controls_and_audit.sql\`; and admin-only \`api/management.js\`.

The source management API uses global tables without organization ownership. Its audit helper suppresses audit-write failures, and quote calculations use JavaScript \`Number\`. Those semantics are not copied.

## Implemented portable slice

| Hatchable source | Portable target | Mapping |
|---|---|---|
| \`clients\` | \`construction_clients\` | Tenant-owned display name and city only; phone, full address and free-text notes excluded initially. |
| \`projects\` | \`construction_projects\` | Tenant-owned project, same-tenant client composite key, work/contract type, dates, status, progress, location label and PKR minor-unit contract amount. |
| Quote \`line_items\` / BOQ | \`construction_boq_items\` | Normalized project lines, Decimal quantity and integer PKR minor-unit rate/total. |
| \`quotes\` | \`construction_quotes\` + \`construction_quote_lines\` | BOQ-only quote snapshots, integer PKR minor units, overhead/profit/discount/tax estimate fields and bounded draft/sent/accepted/rejected/expired lifecycle. |
| \`pricing_items\` | Not implemented | Tenant rate-book governance and pricing-item moderation remain separate work. |
| \`transactions\` | \`construction_cashbook_entries\` + \`journal_entries\` | Project cashbook events are linked to balanced, append-only double-entry journal postings; no source rows are copied. |
| \`labour_entries\`, \`material_entries\` | Not implemented | Labour and material registers remain separate work. |

All endpoints require organization membership and an active, verified, fee-cleared, age-cleared \`construction_provider\` entitlement. Project updates require the creator or organization owner/admin; only owner/admin may approve. Status transitions are bounded and closed projects are terminal.

BOQ quantity is \`NUMERIC(12,3)\` / Python \`Decimal\`; money is integer PKR minor units and uses \`ROUND_HALF_UP\`. Currency is restricted to PKR until ISO 4217 registry and currency-exponent metadata exist. Composite foreign keys prevent cross-tenant client/project references. Audit events commit in the same transaction.

## Regression and negative tests

Missing entitlement and unverified email are denied; cross-tenant references and non-member access are denied; non-creator members cannot update another user's project; zero quantity and duplicate line numbers are rejected; invalid status jumps and premature completion are rejected. SQLite foreign keys are enabled in route tests. Dedicated PostgreSQL integration verifies composite tenant foreign keys and Decimal BOQ persistence.

## Explicitly not implemented

No Hatchable rows or files were exported, copied, reconciled or imported. No source-to-target identity mapping exists. Tenant rate-book governance, labour, materials, contractor hiring, attachments, notifications, external payments, tax/FX and production migration remain out of scope. The live Hatchable application was not modified.

This is a first portable construction slice, not a complete construction vertical or production-readiness claim.


## Draft PR #27 — BOQ-based construction quote lifecycle

Read-only inspection of the live Hatchable Version 40 database confirmed the source `quotes` columns: `quote_no`, optional `client_id`, customer/project labels, calculation mode, area and per-square-foot rates, lump sum, BOQ subtotal, overhead/profit/discount/tax percentages and amounts, total, JSONB line items, status, validity days/date, notes and terms. The source `pricing_items` table stores category, item name, unit, numeric unit rate and active flag. The live `api/management.js` quote handler is owner/admin-only, uses global tables and JavaScript `Number`; those semantics are not copied.

The portable slice is deliberately narrower and tenant-scoped: quotes are generated from a project’s existing BOQ only. Each quote stores a snapshot of line descriptions, quantities, unit rates and line totals, so later BOQ edits do not silently rewrite an issued quote. Money remains integer PKR minor units; quantities and percentage inputs use `Decimal` with half-up rounding. The first calculation mode is BOQ; per-square-foot, lump-sum and mixed modes remain unimplemented until a governed tenant rate book exists.

Implemented in the focused Draft PR:

- `construction_quotes` and `construction_quote_lines` under migration `0038_construction_quotes`.
- Tenant-scoped create/list/detail routes, with active `construction_provider` entitlement and legacy organization membership checks.
- Bounded quote lifecycle: `draft → sent → accepted/rejected`, with expiry checks and owner/admin-only manual customer-decision recording.
- Transactional audit events; no email/SMS/WhatsApp delivery, external signature, payment capture or tax-compliance claim.
- Regression tests cover Decimal totals and rounding, BOQ snapshot stability, send/accept/expiry, missing BOQ, invalid percentages and member decision denial.

No Hatchable rows, private contact fields, identity documents, property deeds or media were exported or copied. The live Version 40 project remains unchanged. PR #27 is implemented but its exact-head CI and review are pending until GitHub confirms them.



## Draft PR #28 — Project cashbook linked to the double-entry ledger

The read-only Hatchable Version 40 schema confirms that source `transactions` contains `project_id`, `direction`, `category`, `amount`, `description` and `transaction_date`. The legacy admin-only `api/management.js` writes global rows and converts money through JavaScript `Number`; those semantics and all live rows are deliberately not copied.

The portable cashbook adds `construction_cashbook_entries` as a project-scoped index of financial events, each linked one-to-one to an immutable `journal_entries` record. It does not maintain a second balance store:

- Income posts debit to an active same-tenant asset account and credit to a same-tenant revenue account.
- Expense posts debit to a same-tenant expense account and credit to an active asset account.
- Both accounts must use the project's currency (currently PKR); amounts are positive integer minor units.
- Every post requires an organization-scoped active `construction_provider` entitlement, existing membership, project creator or owner/admin authority, and an `Idempotency-Key`.
- Only approved, in-progress or on-hold projects accept new entries. Configured accounting periods must have an open period covering the current UTC date.
- Journal entry, cashbook row and audit events commit in one transaction. Duplicate keys replay the original result only for the identical request fingerprint; key reuse with different content returns conflict.
- Composite foreign keys enforce that the project and journal entry belong to the same organization. The original cashbook event is retained; corrections use the existing journal reversal API rather than editing/deleting the cashbook record.

Regression coverage includes income/expense balancing, idempotent replay and payload mismatch, foreign-tenant and wrong-type/currency accounts, same-account and non-positive amount rejection, non-creator member write denial with tenant read, closed-project/closed-period denial, and SQLite/PostgreSQL migration checks for composite tenant foreign keys and database-enforced append-only cashbook rows.

This is a project cashbook posting slice, not bank integration, payment collection, tax, FX, supplier settlement or a full accounting close workflow. No Hatchable rows/files were exported or imported, and the live Version 40 application was not changed.


## Concurrency hardening in the refreshed integration

Project-row locks serialize BOQ writes, project lifecycle transitions, quote snapshot creation and quote send/decision against concurrent project changes. Quote send/decision also lock the tenant- and project-scoped quote row. Cashbook posting locks the project, selected accounts and configured accounting-period rows. Read-only project/quote queries do not request write locks. PostgreSQL-dialect regression tests verify tenant predicates and `FOR UPDATE`; SQLite intentionally does not emulate PostgreSQL row-lock semantics.


## Draft PR #79 — Project labour and material registers

### Verified Version 40 source contract

Read-only inspection of the live HIJAZ ONE Version 40 PostgreSQL schema and source files confirms:

- labour_entries: project_id, worker_name, work_date, worker_count, days NUMERIC(8,2), daily_rate NUMERIC(14,2), total_amount NUMERIC(14,2), notes, created_at.
- material_entries: project_id, item_name, quantity NUMERIC(12,3), unit, unit_price NUMERIC(14,2), total_amount NUMERIC(14,2), supplier, entry_date, notes, created_at.
- The source api/management.js is admin-only. GET lists the two registers; POST creates them. It has no labour/material update or delete action. The source tables are global and do not enforce organization ownership.
- The source uses JavaScript Number arithmetic and stores decimal PKR amounts. Those semantics are not copied.

### Portable implementation

The Draft implementation adds organization/project-scoped construction_labour_entries and construction_material_entries under migration 0048_construction_labour_mat, following the PR #78 chain at 0047_workforce_message_appeals.

- Source worker/date/count/days/rate and material/item/quantity/unit/rate/supplier/date fields are mapped into typed tenant-owned models.
- Money is stored as integer PKR minor units; quantity and days use Python Decimal and ROUND_HALF_UP. Totals are calculated server-side, never accepted from the request.
- Composite (project_id, organization_id) foreign keys prevent cross-tenant references.
- All routes require organization membership and an active construction-provider entitlement. Because worker names and supplier labels are private project records, create/list access is further restricted to the project creator or organization owner/admin.
- The source exposes no edit/delete action; the portable register is append-only with PostgreSQL and SQLite update/delete triggers. Corrections are not silently overwritten.
- Audit events record project/date/count/quantity/amount metadata but intentionally omit worker names and supplier labels.
- No automatic journal posting is performed: the source labour/material tables are operational registers, separate from the source transactions table. Automatic accounting treatment remains a separately reviewed workflow.
- No identity documents, worker phone numbers, attachments, or live source rows are copied.

### Tests and release boundary

Regression coverage includes Decimal rounding, server-computed totals, invalid/overflow numeric values, missing role/tenant isolation, project-creator/owner/admin access, audit PII minimization, composite tenant foreign keys, append-only enforcement, SQLite migration from empty database and PostgreSQL schema/integration.

This PR is stacked on Draft PR #78 and must be reconciled after the role/vertical dependency chain is reviewed. It does not complete the construction vertical: tenant rate-book governance, supplier settlement, labour attendance/payroll, procurement integration, tax/FX, export/import reconciliation and production migration remain open. The live Hatchable Version 40 application remains untouched.


### CI correction after Run #964

The first exact-head CI exposed two independent portability defects before any data was touched:

- PostgreSQL's standard Alembic version table uses VARCHAR(32); the initial revision identifier exceeded that limit. The migration file name remains descriptive, but the revision ID is now the 28-character `0048_construction_labour_mat`.
- The SQLite migration test registered its foreign-key pragma listener after the first connection had already been opened. The listener is now registered immediately after engine creation, before schema inspection, so the composite tenant foreign keys are actually exercised rather than silently skipped.

Run #964 is recorded as failed: PostgreSQL migration stopped at the Alembic revision-length check, and Python 3.11/3.13 exposed the SQLite foreign-key test-order defect. The corrected commit must receive its own exact-head CI; Run #964 is not a pass.
