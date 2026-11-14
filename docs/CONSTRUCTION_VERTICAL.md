# HIJAZ ONE Construction Vertical — Portable Project and BOQ Foundation

**Status:** Implemented in Draft PR #26; not merged or deployed.

## Source inventory and evidence

Read-only inspection of the live Hatchable Version 40 project confirmed 46 database tables, 75 files, 25 active API functions, and 14 SQL migrations. Construction source evidence includes \`clients\`, \`projects\`, \`transactions\`, \`labour_entries\`, \`material_entries\`, \`pricing_items\`, and \`quotes\`; migrations \`0001_construction_management.sql\`, \`0002_quotes_and_pricing.sql\`, \`0003_enterprise_quote_controls_and_audit.sql\`; and admin-only \`api/management.js\`.

The source management API uses global tables without organization ownership. Its audit helper suppresses audit-write failures, and quote calculations use JavaScript \`Number\`. Those semantics are not copied.

## Implemented portable slice

| Hatchable source | Portable target | Mapping |
|---|---|---|
| \`clients\` | \`construction_clients\` | Tenant-owned display name and city only; phone, full address and free-text notes excluded initially. |
| \`projects\` | \`construction_projects\` | Tenant-owned project, same-tenant client composite key, work/contract type, dates, status, progress, location label and PKR minor-unit contract amount. |
| Quote \`line_items\` / BOQ | \`construction_boq_items\` | Normalized project lines, Decimal quantity and integer PKR minor-unit rate/total. |
| \`quotes\`, \`pricing_items\` | Not implemented | Quote lifecycle, rate-book moderation, tax policy and customer delivery remain separate work. |
| \`transactions\`, \`labour_entries\`, \`material_entries\` | Not implemented | Cashbook, workforce/labour and material registers remain separate work. |

All endpoints require organization membership and an active, verified, fee-cleared, age-cleared \`construction_provider\` entitlement. Project updates require the creator or organization owner/admin; only owner/admin may approve. Status transitions are bounded and closed projects are terminal.

BOQ quantity is \`NUMERIC(12,3)\` / Python \`Decimal\`; money is integer PKR minor units and uses \`ROUND_HALF_UP\`. Currency is restricted to PKR until ISO 4217 registry and currency-exponent metadata exist. Composite foreign keys prevent cross-tenant client/project references. Audit events commit in the same transaction.

## Regression and negative tests

Missing entitlement and unverified email are denied; cross-tenant references and non-member access are denied; non-creator members cannot update another user's project; zero quantity and duplicate line numbers are rejected; invalid status jumps and premature completion are rejected. SQLite foreign keys are enabled in route tests. Dedicated PostgreSQL integration verifies composite tenant foreign keys and Decimal BOQ persistence.

## Explicitly not implemented

No Hatchable rows or files were exported, copied, reconciled or imported. No source-to-target identity mapping exists. Quotes, rate-book governance, cashbook/journal posting, labour, materials, contractor hiring, attachments, notifications, external payments, tax/FX and production migration remain out of scope. The live Hatchable application was not modified.

This is a first portable construction slice, not a complete construction vertical or production-readiness claim.
