# Attestation-governed staged release

`app/ai/governed_release.py` composes two independent trust checks:

1. A trusted sandbox controller's HMAC-SHA-256 attestation, bound to the exact
   candidate artifact digest and containing successful test, security-scan, and
   regression evidence.
2. A signed human approval, bound to the same artifact digest and the requested
   action, with actor allowlisting, expiry, freshness, and one-time nonce checks.

## Decision flow

- A missing, malformed, stale, replayed, mismatched, or failed sandbox attestation
  denies the change.
- Caller-provided sandbox/test booleans are ignored. Only the signed attestation
  supplies those values.
- The artifact SHA-256 is recomputed from the exact bytes before any nonce is used.
- A high/critical-risk change or production release returns
  `require_human_approval` without consuming the attestation nonce when approval
  is absent. The same valid attestation can therefore be presented again with
  the subsequent signed approval.
- An invalid human approval is rejected before either nonce is consumed.
- Once all policy checks pass, the attestation nonce is consumed first and the
  human-approval nonce second. Both callbacks must use durable atomic storage;
  PostgreSQL-backed nonce storage is required for multi-worker production.
- A valid low/medium-risk change without a production-release request is only
  `sandbox_only`; this module never merges a PR or deploys.

## Trust and operational boundaries

- HMAC keys must be held by trusted services/secret managers and never exposed to
  model prompts, candidate code, or task payloads.
- HMAC proves possession of a shared key, not a human identity. Human approval
  signing must happen only after a trusted identity/RBAC service authenticates
  and authorizes the actor.
- The two nonce callbacks are separate. If attestation nonce consumption succeeds
  but approval nonce storage fails, the operation fails closed and the proposal
  must be revalidated to obtain fresh evidence.
- This module is a deterministic policy helper, not an HTTP approval service,
  GitHub writer, release executor, or deployment controller.
