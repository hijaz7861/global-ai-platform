# Universal AI Connector Hub

## Purpose

Provide one governed integration layer through which the Global AI Platform can call approved AI providers and expose explicitly authorized platform capabilities to compatible AI clients. The Hub is a shared platform capability, not an unrestricted proxy and not a promise that every vendor or ChatGPT plan supports every connection mode.

## Two connection directions

### 1. Platform-to-provider (outbound)

The platform calls provider APIs through provider-specific adapters behind a common internal interface. Initial candidates:

- OpenAI API, including supported Responses API workflows and tools
- Anthropic Claude API
- Google Gemini API
- OpenAI-compatible endpoints, including approved local/self-hosted runtimes
- Additional providers only after capability, privacy, pricing, terms, and security review

Provider support is adapter-based. Do not assume all providers share the same request format, tool semantics, model capabilities, context limits, or pricing. Record provider/model capability metadata and test each adapter independently.

### 2. AI-client-to-platform (inbound)

Expose a separately authenticated Model Context Protocol (MCP) server so compatible AI clients can discover and call a curated set of platform tools. This can enable supported ChatGPT custom MCP apps and other MCP-capable clients to query or act on platform data, subject to each client's product, plan, workspace, and permission requirements.

The inbound MCP server is not the same thing as calling the OpenAI API. Connecting a platform to ChatGPT through a custom app/MCP endpoint does not grant access to ChatGPT's private internal runtime, account, or subscription, and is not a way to bypass product permissions. A public HTTPS endpoint or supported secure tunnel may be required by the client. Keep it disabled until authentication, authorization, and exposure controls are validated.

## Reference architecture

1. **Connector registry** — tenant-owned or platform-approved connector records; provider, endpoint, capability, status, owner, scope, and health metadata.
2. **Credential vault interface** — encrypted-at-rest secrets, redacted reads, key rotation, explicit secret references, and deployment secret-manager integration. Never return secrets through API responses or logs.
3. **Adapter layer** — provider-specific authentication, request/response normalization where safe, timeouts, bounded payloads, error classification, cancellation, and capability declarations.
4. **Policy and routing engine** — allowlisted providers/models, task-to-capability mapping, data sensitivity rules, per-tenant/user budgets, rate limits, quotas, and explicit fallback policy. No silent provider switch for sensitive data.
5. **Tool gateway** — typed schemas, input validation, tenant-scoped service calls, per-tool permissions, read/write classification, and idempotency for mutating actions.
6. **Approval gate** — human confirmation for payments, refunds, financial postings, deleting/exporting sensitive data, sending external messages, permission changes, and other high-impact actions.
7. **Audit and observability** — connector lifecycle events, provider/model, latency, usage and cost when supplied by provider, outcome, actor, tenant, approval record, and correlation ID. Do not log API keys, access tokens, raw sensitive prompts, or full customer payloads by default.
8. **Admin experience** — connection test, capabilities, scope review, budget limits, health, revocation, audit history, and a safe disabled-by-default state.

## Security requirements

- Enforce tenant isolation and role-based authorization in the service layer; never trust tenant IDs supplied by a model or tool argument without checking the authenticated principal.
- Use OAuth authorization-code flow with state/PKCE where supported; request minimal scopes and support expiry, refresh, revocation, and reauthorization.
- Keep provider credentials server-side and out of source control, browser storage, tool outputs, prompts, traces, and error messages.
- For inbound MCP, require authenticated clients, explicit tool allowlists, strict schemas, bounded calls, origin/network controls where appropriate, and per-tool authorization. Use HTTPS in deployed environments.
- For webhooks, verify signatures against the raw request body, enforce timestamps and replay protection, use idempotency keys, and keep retries bounded.
- Treat model output, connector responses, documents, web pages, and MCP tool descriptions as untrusted data. Defend against prompt injection and cross-tenant data exfiltration.
- Default to read-only tools. Separate read and write scopes. Never allow a model to grant itself permissions.
- Require human approval before high-impact or irreversible actions; approval must bind to the exact action and arguments and expire.
- Apply timeouts, response-size limits, concurrency limits, rate limits, budget ceilings, and circuit breakers. Avoid automatic retries for non-idempotent writes.
- Provide disconnect/revoke controls and remove or disable associated tokens promptly.

## Suggested initial tool groups

Start with read-only, low-risk tools:
- platform.health
- organization.get_current
- catalog.search_public
- orders.get_own_summary
- accounting.get_report (authorized read-only reports only)
- connector.list_capabilities (never expose secret values)

Later, add separately permissioned write tools such as creating a draft listing, drafting a purchase request, or creating an invoice draft. Financial posting, issuing refunds, changing roles, sending messages, and deleting data must require explicit approval and dedicated tests.

Tool names above are design examples, not claims that these endpoints currently exist.

## Rollout phases and acceptance criteria

### Phase 0 — contract and threat model
- Document provider/client directions, data classifications, supported auth modes, scope model, threat model, and failure semantics.
- Define an adapter contract and connector lifecycle states: draft, pending_authorization, active, degraded, revoked, error.
- No external calls or public MCP endpoint yet.

### Phase 1 — provider registry and one outbound adapter
- Store non-secret metadata and secret references.
- Add one provider adapter behind the existing guarded AI service; keep live calls disabled by default.
- Test bad credentials, timeout, oversized responses, redirect rejection, quota exhaustion, audit redaction, tenant boundaries, and provider failure.
- Enforce a per-tenant budget and explicit opt-in before any live call.

### Phase 2 — inbound MCP read-only pilot
- Expose only a small, read-only allowlist backed by existing authorization-aware service functions.
- Require authentication, bounded request/response sizes, rate limits, and audit events.
- Test cross-tenant attempts, missing/revoked credentials, prompt-injection-like tool output, and unauthorized tool invocation.
- Publish no public endpoint until a staging review approves it.

### Phase 3 — OAuth and webhooks
- Implement least-privilege OAuth scopes, state/PKCE where supported, token refresh/revocation, signed webhooks, replay protection, idempotency, and bounded retry queues.
- Test authorization denial, scope reduction, token expiry, replayed events, duplicate delivery, and secret rotation.

### Phase 4 — controlled write actions and routing
- Add approval-bound write tools, provider capability routing, usage/cost accounting, budgets, circuit breakers, and tested fallback policy.
- Use sandbox or mock providers in CI; do not make paid live calls in CI.
- Require security review and operator sign-off before production enablement.

## Non-goals and limitations

- The Hub cannot make paid provider APIs free, bypass model provider terms, or guarantee identical capabilities across providers.
- ChatGPT custom MCP app availability depends on ChatGPT product, plan, workspace, and current feature support. API access and ChatGPT subscription access are separate.
- A connector is not complete merely because a registry record or route exists; completion requires implementation, security tests, integration tests, documentation, and operational evidence.
- This document is a design specification. It does not claim that the full Hub, OAuth lifecycle, webhook system, or inbound MCP server has been implemented.
