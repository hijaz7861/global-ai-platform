# AI Autonomy and Change Release Gate

## Purpose

The optional AI CTO may propose changes, inspect evidence, and prepare a change for review. The initial implementation in `app/ai/autonomy_gate.py` is a deterministic policy gate that decides whether a proposal must be denied, remain sandbox-only, require human approval, or is eligible for a staged release.

It is not an AI agent, code executor, malware scanner, or sandbox runtime. It must not be presented as providing those capabilities.

## Required lifecycle

1. Receive a proposal from an authenticated, authorized actor.
2. Store the exact patch/artifact in immutable or versioned storage.
3. Compute SHA-256 on the exact artifact bytes and compare against the recorded digest. A digest detects accidental or malicious content changes relative to a trusted expected value; it does not authenticate the proposer.
4. Execute the proposal only in an isolated disposable runner with no production credentials, no production network access by default, resource/time limits, and an allowlisted dependency source.
5. Collect signed or otherwise authenticated evidence for tests, regression tests, security checks, resource usage, and sandbox identity.
6. Pass that evidence to the deterministic gate. Missing or failed evidence denies release.
7. Keep changes sandbox-only by default. High/critical risk and all production releases require human approval. Production release additionally requires a verified rollback plan.
8. Deploy only through a separate, least-privilege staged deployment service. The gate itself must never execute commands or deploy code.
9. Record the actor, artifact digest, evidence references, decision, approvals, and release/rollback outcomes in an append-only audit trail.

## Trust boundaries

- Treat model output, repository files, issue text, webpages, tool results, and test output as untrusted input.
- Never let an AI-generated plan grant itself permissions or alter the policy gate.
- Use isolated workers; do not run proposed code inside the API process.
- Do not pass production secrets to sandbox jobs.
- Protect approval actions against CSRF/replay and bind each approval to the exact artifact digest, test evidence, environment, and release scope.
- Require explicit authorization and tenant scoping for every connector/tool operation.
- SHA-256 is a digest, not encryption, identity proof, or a replay defense. Use authenticated transport and protocol-appropriate signatures/HMAC where request authenticity is required; use nonces, timestamps, and durable idempotency records where replay is a risk.
- AI-generated changes to authentication, authorization, secrets, audit logging, cryptography, payments, tenant isolation, destructive data operations, or deployment controls are high/critical risk by default.

## Current implementation boundary

The policy gate and its unit tests are a first incremental component. The following remain unimplemented until separate code and evidence exist: authenticated evidence provenance, actual disposable sandbox provisioning, resource/network isolation, scanner integrations, queue/workflow persistence, admin UI and RBAC, signed approvals, append-only audit integration, staged deployment, monitoring, and automated rollback. Do not connect this gate to production deployment until these controls have been implemented and independently tested.
