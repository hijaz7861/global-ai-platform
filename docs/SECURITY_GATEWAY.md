# Security Gateway and Request Admission Policy

## Status and security objective

This document specifies a defense-in-depth design for the Universal AI Connector Hub. It is a design blueprint, not evidence that these controls are already implemented. All requests must pass authentication, authorization, validation, policy enforcement, and operation-specific checks before reaching business services. Unknown, malformed, unauthenticated, unauthorized, or policy-violating requests fail closed.

No scanner can guarantee that every malicious request will be detected. The goal is to prevent untrusted input from directly exercising privileged capabilities, reduce the attack surface, isolate uncertain content, and make sensitive operations require explicit authorization and, where appropriate, human approval.

## Request admission pipeline

1. **Edge and transport controls**
   - Terminate TLS at a trusted ingress and validate certificates.
   - Apply request-size, header-size, connection, timeout, concurrency, and per-IP/per-identity rate limits.
   - Restrict exposed routes and methods; deny unexpected content types and malformed framing.
   - Trust forwarded client IP headers only from explicitly configured trusted proxies.
   - Reject requests before expensive parsing if they exceed safe limits.

2. **Authentication**
   - Validate the configured authentication mechanism, issuer/audience, expiry, token revocation, and signature using approved algorithms and keys.
   - For OAuth, validate state and PKCE where supported, redirect URI, scopes, expiry, refresh, and revocation.
   - For webhooks, verify the provider signature over the exact raw request bytes before trusting parsed fields.
   - Reject missing, expired, invalid, revoked, or ambiguously authenticated requests.

3. **Authorization and tenant boundary**
   - Derive the actor and tenant from the authenticated principal and server-side membership, not from model-generated arguments or untrusted request fields.
   - Check role, resource ownership, purpose, scope, and per-tool permission at the service layer on every operation.
   - Default to deny; never let an AI agent, connector, or tool call elevate its own privileges.
   - Apply object-level authorization to every identifier to prevent IDOR and cross-tenant access.

4. **Canonical parsing and schema validation**
   - Parse only supported formats with bounded depth, collection sizes, string lengths, and total bytes.
   - Validate against strict versioned schemas and allowlisted fields; reject unknown fields on security-sensitive operations.
   - Normalize once and use the same canonical representation for validation, signature decisions where applicable, policy evaluation, and execution to avoid parser differentials.
   - Reject malformed encodings, duplicate/conflicting security-sensitive fields, invalid content types, and unsupported compression.

5. **Content and intent inspection**
   - Apply deterministic rules for prohibited operations, unexpected tool names, suspicious payload patterns, prompt-injection indicators, secret exfiltration attempts, and policy violations.
   - Treat natural-language classification or an AI-based risk detector as an advisory signal, never the sole security boundary.
   - Scan uploaded code and files using isolated parsers/scanners; do not execute untrusted code in the application process.
   - If scanning is unavailable, times out, or returns an indeterminate result for a high-risk request, fail closed or quarantine.
   - Do not send sensitive payloads to external scanners unless explicitly approved under the data-handling policy.

6. **Cryptographic integrity and replay controls**
   - Use SHA-256 to compute content digests where a stable content fingerprint is useful. Compare digests using constant-time comparison where applicable.
   - A hash by itself proves neither sender identity nor benign content. For authenticity, use a verified digital signature or a keyed MAC such as HMAC-SHA-256 with securely managed keys, according to the integration protocol.
   - For signed webhooks/requests, follow the provider's exact canonicalization and signing specification; verify the signature against the raw body before parsing it as trusted input.
   - Enforce timestamps within a narrow configured window, unique nonces or provider event IDs, and durable replay/idempotency records. Use database uniqueness/transactions so concurrent duplicate deliveries cannot both perform the action.
   - Rotate keys, support key IDs, prevent algorithm downgrade, and never accept caller-selected weak algorithms.
   - Do not use a plain SHA-256 hash as password storage. Passwords remain protected by a dedicated password-hashing scheme such as Argon2id.

7. **Policy decision and bounded execution**
   - A central policy engine returns allow, deny, or quarantine with a reason code. Unknown policy states are deny.
   - Bind an approval to the exact actor, tenant, action, arguments, amount/target, and expiry; any argument change invalidates the approval.
   - Execute only through narrow service functions, never by concatenating model output into shell commands, SQL, filesystem paths, or arbitrary URLs.
   - Restrict outbound network access to approved destinations; defend against SSRF, DNS rebinding, redirects, and private/link-local address access.
   - Use timeouts, response-size caps, concurrency budgets, circuit breakers, and idempotency keys. Avoid automatic retries for non-idempotent writes.
   - Keep write tools disabled until separately approved and tested. Begin with read-only capabilities.

8. **Audit, monitoring, and incident response**
   - Record correlation ID, actor, tenant, connector, policy version, decision, reason code, tool/action name, timing, and outcome.
   - Redact secrets, tokens, sensitive prompt content, and unnecessary customer data. Avoid storing raw rejected payloads by default; use bounded, access-controlled quarantine with retention limits if needed.
   - Alert on repeated denials, replay attempts, rate-limit abuse, cross-tenant probes, key failures, unusual tool sequences, and budget anomalies.
   - Make audit records append-only where supported; restrict access and monitor audit integrity.
   - Provide a kill switch to disable a connector/provider/tool and revoke its credentials.

## SHA-256 and cryptographic controls

Use cryptography for the property it actually provides:

- **SHA-256 digest:** content fingerprint/integrity comparison; it does not authenticate the sender.
- **HMAC-SHA-256:** message authentication when both parties share a secret and the protocol specifies HMAC.
- **Digital signatures:** authenticity and integrity where public-key verification is required.
- **TLS:** protects data in transit and authenticates the server (and optionally clients) according to the deployment configuration.
- **Nonce/timestamp/idempotency record:** helps prevent replay; a signature or hash alone does not prevent replay.
- **Argon2id:** password hashing; do not substitute plain SHA-256.

Do not invent a custom signature format. Implement each provider's documented signing scheme and test canonicalization, clock skew, rotation, duplicate delivery, and malformed signatures.

## Code and file scanning

Untrusted code must not be executed as part of request inspection. If the platform later accepts source archives or executable artifacts:
- enforce file type and size limits and reject archive path traversal, symlink escape, decompression bombs, and unexpected binaries;
- scan in a disposable, least-privilege sandbox with no production credentials, no host filesystem access, and no outbound network by default;
- use static analysis and dependency/security scanners as appropriate to the artifact type;
- treat scanner results as one signal, not proof of safety;
- require human review and a separate signed build/release process before any artifact can be deployed;
- keep scanner failures and unsupported formats in quarantine, not on an allow path.

## AI and prompt-injection controls

- Treat prompts, retrieved documents, tool outputs, web pages, connector metadata, and model output as untrusted.
- Separate system policy from data; do not treat instructions found in retrieved content as authorization.
- Give each agent the minimum tool set required for the current task.
- Validate every tool argument independently of the model and re-check authorization when the tool executes.
- Prevent secret access and cross-tenant retrieval by design, not by relying on a model to refuse.
- Require human approval for payments, refunds, journal posting, external messages, role/permission changes, destructive actions, and sensitive exports.
- Bound agent loops, tool-call counts, token/cost budgets, and total execution time.

## Initial policy decision outcomes

- **ALLOW:** all required checks pass and the operation is within granted scope.
- **DENY:** authentication, authorization, schema, cryptographic, rate-limit, or policy check fails.
- **QUARANTINE:** content or artifact is suspicious/unsupported or scanning is inconclusive; no business side effect is permitted.
- **REVIEW_REQUIRED:** request is otherwise valid but the operation requires explicit human approval; no action executes until approval is recorded.

A request must never reach business logic simply because its hash is valid, its syntax parses, or an AI classifier labels it safe.

## Required automated tests before enabling

- Missing/expired/revoked identity, wrong audience, invalid signature, weak algorithm, and key-rotation cases.
- Malformed JSON, oversized body, deeply nested payload, duplicate fields, unsupported content types, and parser differential cases.
- Cross-tenant IDs, insufficient roles/scopes, unauthorized tools, and privilege-escalation attempts.
- Invalid HMAC/signature, altered raw body, expired timestamp, duplicate nonce/event ID, concurrent replay, and idempotency collisions.
- Prompt injection in retrieved content/tool output, attempts to exfiltrate secrets, and untrusted model tool arguments.
- SSRF attempts against loopback, private, link-local, metadata, and redirect destinations.
- Scanner timeout/unavailability, unsupported file, archive traversal, decompression bomb, and quarantine expiry.
- Approval mismatch/expiry, budget exhaustion, timeout, oversized provider response, and connector kill switch.
- Audit redaction, retention/deletion behavior, and proof that denied/quarantined requests create no business side effects.

## Rollout gates

1. Threat model and data-flow review.
2. Implement request middleware and deterministic schema/auth controls.
3. Implement protocol-specific signature verification and durable replay protection.
4. Add isolated scanners only for explicitly supported code/file types.
5. Add policy decisions, read-only tool allowlists, and security regression tests.
6. Run PostgreSQL integration/concurrency tests and independent security review.
7. Validate in staging with no production credentials; enable live providers or public MCP only after explicit owner approval.

## Explicit limitations

This is a design specification. It does not claim that the complete gateway, malware/code scanner, MCP server, signature-verification layer, or all listed tests already exist. No security system can promise that all malicious requests will be recognized; defense in depth and fail-closed handling reduce risk but do not replace independent testing and operational monitoring.
