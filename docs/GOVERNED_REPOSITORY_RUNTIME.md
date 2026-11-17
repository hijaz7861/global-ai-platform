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


## Worker-driven proposal validation

A separate durable task type, `proposal.validate`, connects the task worker to the
existing rootless Docker proposal-validation suite. Submission is disabled unless
`AI_SANDBOX_ENABLED=true`, PostgreSQL is configured, and the caller is an
organization owner/admin. The worker re-checks current membership before running.

Only candidate file contents are accepted from the task. The sandbox image must
be digest-pinned; the trusted test directory and the three argv command arrays
(unit, security, regression) come exclusively from operator-controlled runtime
configuration. Commands are passed as argv (never through a shell), and each
check runs in a fresh no-network, read-only, resource-limited container. The
worker renews its task lease between checks.

A signed attestation is returned only when all three checks pass. Task results
contain the canonical proposal digest, paths, bounded check metadata and (on
success) the signed attestation; raw process output and signing keys are never
returned. The task cannot create a GitHub branch or pull request. The separate
human-approved endpoint remains the only repository-write path.

Required settings:

- `AI_SANDBOX_ENABLED=false` by default
- `AI_SANDBOX_IMAGE`: full image reference pinned with `@sha256:<64 hex>`
- `AI_SANDBOX_TRUSTED_TEST_DIRECTORY`: trusted tests mounted read-only on the worker host
- `AI_SANDBOX_VALIDATION_COMMANDS`: JSON object with exactly `unit`, `security`,
  and `regression` argv arrays
- `AI_SANDBOX_ATTESTATION_HMAC_KEY`: at least 32 bytes, held only by the trusted worker/API runtime

Rootless Docker, image availability, trusted test contents, and actual command
behavior must be verified on the deployed worker host before enabling this feature.


## Autonomous repository proposal generation

The durable task type `proposal.generate` reads a bounded set of UTF-8 source files
from the allowlisted repository's default branch using a short-lived GitHub App
installation token scoped to `contents: read` only. Read-only and writer tokens
have separate caches and permission requests; generation never receives the
writer token's `contents: write` or `pull_requests: write` permissions.

The reader excludes hidden, credential, authentication, security, payment,
migration, and cryptographic paths; enforces file/count/byte limits; rejects
truncated repository trees; and verifies every fetched Git blob against its Git
object SHA before including it in model context.

The model receives repository files as untrusted data and must return a strict
JSON proposal. The worker validates file paths, modes, UTF-8 content, sensitive
path restrictions, and total size, then returns the proposal digest and file
contents as a durable task result. It does not modify GitHub or execute generated
code. The result must pass the separate `proposal.validate` task before it can
be presented for human approval and draft PR creation.

Enable only in a trusted staging/production runtime with PostgreSQL, a live AI
provider, a positive daily request quota, a GitHub App installation, and an
explicit repository allowlist. The feature is disabled by default with
`AI_PROPOSAL_GENERATION_ENABLED=false`.


## Repository-bound validation and task chaining

A completed `proposal.generate` task can be passed directly to
`proposal.validate` using only its `source_task_id`. The worker accepts the
source only when it belongs to the same organization and authenticated creator,
has task type `proposal.generate`, completed successfully, and contains a valid
proposal result. It recomputes the canonical proposal digest before sandboxing.

For inline validation, the request must include the allowlisted repository,
its 40-character base commit SHA, and the proposal files. In either mode the
sandbox HMAC attestation signs both the canonical proposal digest and the
repository/base-commit binding.

The draft-PR endpoint requires that signed repository binding to match the
requested allowlisted repository. Before creating any Git objects or branch, the
writer fetches the repository's current default-branch SHA and compares it to
the attested base SHA. If the branch moved after generation/validation, the
write is rejected and a fresh proposal/validation/approval cycle is required.
This prevents a valid attestation for one repository or base revision from
being replayed against another.
