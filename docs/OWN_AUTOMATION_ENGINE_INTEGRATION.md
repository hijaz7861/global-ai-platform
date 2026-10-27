# Own Automation Engine Integration

## Goal

Integrate the separate `hijaz7861/own-automation-engine` repository as the workflow-automation subsystem of Global AI Platform while keeping tenant identity, authorization, approvals, audit records, and business data under the platform's control.

## Verified starting point

- Global AI Platform is in an open, unmerged pull request (#1) from `draft/platform-foundation` to `master`. It is not deployed.
- Own Automation Engine's MCP-server PR #14 is merged into its `main` branch.
- The engine MCP server uses local stdio JSON-RPC. Its exposed tools cover workflow listing, inspection, design, proposal/version inspection, and pending-proposal creation.
- The MCP surface deliberately does not expose workflow apply or execute.
- The engine's tunnel setup guide describes a possible secure connection, but the live tunnel ID, runtime key, client process, and end-to-end connection have not been verified here.
- Global AI Platform currently has a guarded AI chat adapter, not a complete automation orchestration or connector runtime.

## Integration boundary

Global AI Platform remains the control plane and system of record. Own Automation Engine remains the workflow design/execution subsystem. The engine must not create its own tenant identity or treat a caller-supplied tenant ID, role, or approval flag as authorization.

The platform-to-engine adapter must derive the authenticated user and organization from platform credentials and membership records. Every request must be scoped to a platform-generated request ID and organization ID. The engine must not receive platform signing keys, database credentials, or unrelated tenant data.

## Delivery phases

### Phase 1 — contract and safe discovery

- Document the tool contract and map engine workflow identifiers to platform-owned connector records.
- Allow read-only workflow discovery/inspection and proposal creation only.
- Validate tool name, schema, payload size, workflow path, organization scope, request ID, and idempotency key.
- Record allowlisted audit metadata only; never log prompts, credentials, access tokens, or full business payloads.
- Reject unknown tools and any attempt to apply or execute a workflow through the discovery interface.

### Phase 2 — secure transport

- Use the engine's stdio MCP process through the approved secure tunnel/client path; do not expose the local engine port publicly.
- Use a runtime credential with only the required tunnel permissions, stored outside the repository and rotated operationally.
- Add connection/readiness checks, timeouts, bounded responses, cancellation, and sanitized errors.
- Do not mark the connector as connected until a real read-only handshake and tool-list check succeed.

### Phase 3 — platform-owned approvals

- Store proposals and their status in platform-owned records, bound to organization, requester, workflow, payload digest, and expiry.
- Require an authenticated platform user with the appropriate organization role to approve sensitive actions.
- Approval must be a server-side state transition, not a boolean supplied by the engine or client.
- Use idempotency and an audit event for each state transition. Expired, replayed, cross-tenant, or mismatched proposals must fail closed.

### Phase 4 — guarded execution

- Add execution only after the transport, identity mapping, approval records, and audit invariants have passing tests.
- Enforce per-organization allowlists, rate/concurrency limits, timeouts, budget caps, and kill-switch controls.
- Keep payment, publishing, email outreach, inventory changes, destructive actions, and other external side effects disabled unless each action has an explicit policy and approval path.
- Record the execution request/result metadata and verification status without storing secrets or unnecessary sensitive output.
- Add PostgreSQL integration tests for concurrent approvals/idempotency and cross-tenant isolation before enabling execution.

## Initial API contract

The first adapter should expose a narrow internal interface rather than a generic arbitrary-command endpoint:

- `discover_workflows(organization_id, actor_user_id)` — read-only.
- `inspect_workflow(organization_id, actor_user_id, workflow_ref)` — read-only.
- `propose_workflow_change(organization_id, actor_user_id, workflow_ref, goal, idempotency_key)` — creates a pending proposal only.
- `get_proposal_status(organization_id, actor_user_id, proposal_id)` — organization-scoped read.

Do not add `apply_workflow`, `execute_workflow`, arbitrary shell execution, arbitrary URL fetching, or client-controlled approval to this initial contract.

## Release gates

The integration is not complete until all of these are evidenced:

1. Engine tests pass on the exact merged engine revision.
2. Platform unit tests validate schema, permissions, tenant isolation, and failure handling.
3. A real secure-tunnel handshake and read-only tool-list request succeed.
4. PostgreSQL integration tests validate concurrent proposal creation/approval and idempotent replay.
5. Audit records are checked for secret/payload redaction.
6. A staging test proves the platform's authenticated actor and tenant scope are preserved end-to-end.
7. Execution remains disabled until the owner explicitly approves a separate release gate.

## Current status

**Design/contract documented; live connector not connected; workflow execution not integrated or verified.** This is an integration plan and a safety boundary, not a claim that the two applications currently communicate.
