# Governed GitHub repository-write configuration

## Default state

Repository writing is disabled unless `AI_REPOSITORY_WRITES_ENABLED=true` is explicitly
configured. This setting alone does not create a GitHub App, provision credentials, grant
permissions, or enable an external integration.

Keep the feature disabled in development and local SQLite environments. An enabled writer
requires a staging or production environment and PostgreSQL-backed nonce storage.

## Required settings

| Setting | Requirement |
|---|---|
| `AI_REPOSITORY_WRITES_ENABLED` | Explicit opt-in; defaults to `false` |
| `GITHUB_APP_ID` | Numeric identifier, at most 32 digits |
| `GITHUB_APP_INSTALLATION_ID` | Numeric identifier, at most 32 digits |
| `GITHUB_APP_PRIVATE_KEY_PEM` | Valid, unencrypted RSA PEM private key, at least 2048 bits |
| `GITHUB_REPOSITORY_ALLOWLIST` | One to 50 explicit comma-separated `owner/repository` names |
| `AI_SANDBOX_ATTESTATION_HMAC_KEY` | At least 32 UTF-8 bytes, generated randomly |
| `AI_HUMAN_APPROVAL_HMAC_KEY` | At least 32 UTF-8 bytes, generated randomly |

The allowlist rejects empty entries, case-insensitive duplicates, wildcard names, path-like
segments, and leading/trailing dots. Repository names must be explicit; wildcard access is
not supported.

## Secret handling

- Supply all private keys and HMAC keys through an approved secret manager.
- Never commit real values to `.env`, `.env.example`, CI variables in source, issue comments,
  PR descriptions, logs, or model prompts.
- `SecretStr` prevents accidental display of the PEM and HMAC values in settings
  representations. It does not replace secret-manager access controls or rotation.
- Use distinct random HMAC keys for sandbox attestations and human approvals.
- Rotate credentials through the owner-operated secret-management process; this code does
  not create, rotate, or change secrets.

## Authorization sequence

The GitHub App token provider requests a short-lived installation token restricted to the
configured repository. The repository writer is a separate component and must only be called
after the trusted runtime has:

1. validated the proposal and its exact artifact digest;
2. run trusted tests, security checks, and regression checks in an isolated sandbox;
3. verified the signed sandbox attestation and consumed its one-time nonce;
4. verified an action-bound human approval from an authenticated, authorized actor; and
5. confirmed that the repository is present in the immutable allowlist.

HMAC verification proves possession of a shared key, not human identity. The approval issuer
must authenticate the actor independently. No user-submitted approval claim or payment
reference is sufficient.

## Explicit non-capabilities

This configuration change does not:

- create or install a GitHub App;
- provision App permissions, tokens, or secret-manager entries;
- enable repository writes by default;
- authorize writes to the default branch;
- merge pull requests, deploy, or trigger workflows;
- prove branch protection or required-review settings;
- replace human review or owner approval.

The feature remains disabled until an operator provisions and verifies the required
infrastructure and secrets. No real credentials or live provider access are included here.
