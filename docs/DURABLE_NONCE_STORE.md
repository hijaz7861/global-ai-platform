# Durable nonce consumption

`app/ai/nonce_store.py` provides `SQLiteNonceStore.consume_nonce`, a callback compatible with the signed-attestation verifier.

## Guarantees

- A nonce is inserted inside a `BEGIN IMMEDIATE` transaction with a primary-key uniqueness constraint.
- Competing workers sharing the same persistent SQLite file cannot both consume the same nonce.
- Replays return `False`; unexpected storage errors propagate so callers fail closed.
- Old entries are pruned after the configured retention interval. Retention must be at least 930 seconds, exceeding the verifier's default 900-second freshness window and 30-second future skew.

## Deployment limits

- This SQLite implementation is intended for a single host using a persistent, private database file.
- Do not use `:memory:` or an ephemeral filesystem; the implementation rejects `:memory:` explicitly.
- Do not treat a local SQLite file as a cross-host coordination service. Multi-host deployments should use a shared transactional PostgreSQL table or another strongly consistent store with a unique nonce constraint.
- Keep the database outside web-served directories, restrict filesystem permissions, back it up securely, and monitor disk capacity.
- This component prevents replay only when it is actually wired into `verify_attestation(..., consume_nonce=store.consume_nonce)`. It does not itself create signed evidence or execute sandbox jobs.
