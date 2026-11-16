# Governed repository approval runtime

The authenticated endpoint is:

`POST /api/v1/organizations/{organization_id}/ai/repository/draft-pr`

It accepts a bounded proposal and a signed sandbox attestation. The human actor is
never accepted from request data: it is derived from the authenticated access token
and organization membership. Only active, email-verified owners/admins with MFA
enabled may invoke the approval action.

The service creates a five-minute, digest-bound approval for the exact
`repository.pull_request.create` action. It signs that approval server-side using
the configured HMAC key. The endpoint verifies the sandbox attestation and
approval through the existing governed-release gate, with both nonces stored in
the shared PostgreSQL `consumed_approval_nonces` table using
`INSERT ... ON CONFLICT DO NOTHING`.

The nonce transaction is committed before GitHub token minting or any external
write. A failed GitHub call therefore requires a fresh sandbox attestation and
approval; replaying an old request cannot repeat the write. GitHub installation
tokens are minted lazily only after the policy gate succeeds and are restricted
to the exact allowlisted repository.

This endpoint creates a draft pull request only. It does not merge, deploy,
modify the default branch, or trigger a production release. Runtime configuration
must remain disabled by default and must be provisioned through a secret manager.


## Audit trail

Once both signed proofs have passed validation, the API stages an organization-
scoped audit event containing the authenticated actor, repository, canonical
proposal digest, sandbox run ID, approval ID, and an
`authorized_pending_github_write` status. The event is committed in the same
database transaction as the consumed nonces, immediately before requesting the
GitHub installation token.

After GitHub confirms draft PR creation, the event is updated with the PR number,
URL, branch, commit SHA, and `draft_pr_created` status. If the GitHub write fails
after authorization, the durable event is marked `github_write_failed`; it
does not claim that a PR was created. If the final audit update itself fails,
the initial durable authorization event remains available for reconciliation.
