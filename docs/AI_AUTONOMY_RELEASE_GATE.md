# AI Autonomy and Change Release Gate

## Purpose

The optional AI CTO may propose changes, inspect evidence, and prepare a change for review. The implementation currently includes:

- `app/ai/autonomy_gate.py`: deterministic, fail-closed release eligibility decisions.
- `app/ai/evidence_attestation.py`: HMAC-SHA-256 authentication of sandbox evidence, artifact-digest binding, freshness checks, and verification using either an injected nonce callback or the database-backed helper.
- `app/ai/nonce_store.py`: persistent nonce hashes with database uniqueness as an atomic replay barrier.
- Alembic migration `0029_ai_evidence_nonces` and unit tests for the new store.

These are policy/evidence components. They are not an AI agent, code executor, malware scanner, or sandbox runtime.

## Required lifecycle

1. Receive a proposal from an authenticated, authorized actor.
2. Store the exact patch/artifact in immutable or versioned storage.
3. Compute SHA-256 on the exact artifact bytes and compare against the recorded digest. A digest detects content changes relative to a trusted expected value; it does not authenticate the proposer.
4. Execute the proposal only in an isolated disposable runner with no production credentials, no production network access by default, resource/time limits, and an allowlisted dependency source.
5. A trusted sandbox controller signs the test/security/regression result, exact artifact digest, run ID, sandbox ID, issue time, and unique nonce using HMAC-SHA-256. Keep the HMAC key in a secret manager; never expose it to model prompts or candidate code.
6. Verify the attestation and atomically consume its nonce in durable shared storage. The database-backed helper is implemented, but production must run migration 0029 and use a shared supported database. Storage failures propagate as errors so callers can fail closed.
7. Pass independently authenticated evidence to the deterministic gate. Missing, stale, mismatched, replayed, or failed evidence denies release.
8. Keep changes sandbox-only by default. High/critical risk and all production releases require human approval. Production release additionally requires a verified rollback plan.
9. Deploy only through a separate, least-privilege staged deployment service. The gate itself must never execute commands or deploy code.
10. Record the actor, artifact digest, evidence references, decision, approvals, and release/rollback outcomes in an append-only audit trail.

## Trust boundaries

- Treat model output, repository files, issue text, webpages, tool results, and test output as untrusted input.
- Never let an AI-generated plan grant itself permissions or alter the policy gate.
- Use isolated workers; do not run proposed code inside the API process.
- Do not pass production secrets to sandbox jobs.
- Protect approval actions against CSRF/replay and bind each approval to the exact artifact digest, test evidence, environment, and release scope.
- Require explicit authorization and tenant scoping for every connector/tool operation.
- SHA-256 is a digest, not encryption, identity proof, or a replay defense. HMAC authenticates evidence only to holders of its shared secret; it does not protect against a compromised signer. Use authenticated transport, secret rotation, nonces, timestamps, and durable idempotency records.
- AI-generated changes to authentication, authorization, secrets, audit logging, cryptography, payments, tenant isolation, destructive data operations, or deployment controls are high/critical risk by default.
- Scanner failure, unsupported content, missing evidence, and uncertain policy outcomes must fail closed.

## Current implementation boundary

Implemented in this draft: deterministic release gate, HMAC-SHA-256 evidence signing/verification, freshness and artifact-binding checks, database-backed atomic nonce consumption, migration, and unit tests.

Still not implemented: actual disposable sandbox provisioning, hardened resource/network isolation, scanner integrations, trusted evidence-signing service and key rotation, queue/workflow persistence, admin UI and RBAC, signed human approvals, append-only audit integration, staged deployment, monitoring, and automated rollback. Do not connect these modules to production deployment until these controls have been implemented and independently tested.
