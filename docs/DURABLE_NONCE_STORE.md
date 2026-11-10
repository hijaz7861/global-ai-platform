# Durable nonce consumption

Two nonce backends implement the signed-approval/attestation callback contract:

- `app/ai/nonce_store.py` — `SQLiteNonceStore` for local, single-host development.
- `app/ai/postgres_nonce_store.py` — `PostgresNonceStore` for shared multi-worker deployments. Its table is declared in `app.models.ConsumedApprovalNonce` and created by Alembic revision `0034_postgres_approval_nonces`.

## Guarantees

- Both backends expose `consume_nonce(nonce) -> bool`: first use returns `True`, replay returns `False`.
- PostgreSQL uses a primary-key uniqueness constraint and `INSERT ... ON CONFLICT DO NOTHING RETURNING` in a transaction; concurrent workers cannot both consume the same nonce.
- PostgreSQL retention cleanup and nonce insertion occur in the same transaction. Unexpected storage errors propagate so callers fail closed.
- Old entries are pruned after the configured retention interval. Retention must be at least 930 seconds, exceeding the verifier's default 900-second freshness window and 30-second future skew.

## Deployment limits

- SQLite is intended for one host using a persistent, private database file. Do not use `:memory:` or ephemeral storage.
- Production callers must explicitly inject `PostgresNonceStore` backed by the shared PostgreSQL engine and apply migration `0034` first.
- Do not treat a local SQLite file as cross-host coordination. Do not configure independent PostgreSQL databases per worker.
- Keep database access restricted, back up securely, and monitor disk capacity.
- The store prevents replay only when its `consume_nonce` method is wired into the signed verifier. It does not issue approvals, authenticate human identities, create evidence, or execute sandbox jobs.
