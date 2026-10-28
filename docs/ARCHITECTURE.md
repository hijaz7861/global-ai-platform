# Architecture and Current State

## Architecture
Use a modular monolith to reduce cost and operational complexity while domain boundaries and integration contracts mature.

- API: FastAPI with versioned routes and OpenAPI.
- Persistence: SQLAlchemy models and Alembic migrations; SQLite is used for local/unit tests, PostgreSQL 16 is used in CI integration tests and is required for live AI quota enforcement.
- Tests: pytest, Ruff, compile checks, migration consistency checks, dependency audit, Bandit, and dedicated PostgreSQL concurrency/invariant tests.
- Configuration: environment-backed settings; secrets must be supplied by the deployment secret manager.
- Domain modules: identity, organizations/memberships, commerce/catalog/orders, suppliers/procurement, accounting, audit, AI adapter and security maintenance.
- Security: least privilege, tenant-scoped queries, password hashing, rotating refresh sessions, MFA, rate limiting, audit triggers, bounded inputs, and fail-closed AI configuration.
- Integrations: adapters with explicit permissions, timeouts and response-size limits.

## Current implementation
The foundation has an operational API and database migration chain, identity and tenancy, commerce and inventory workflows, procurement and purchase-order lifecycle, double-entry ledger and reports, a guarded OpenAI-compatible AI endpoint, and a CI workflow including PostgreSQL integration tests. Docker startup applies migrations before launching the API.

## Not yet production-proven
Production secrets/SMTP/HTTPS/proxy configuration, scheduler provisioning, observability/alerting, managed database configuration, independent security and accounting review, backup/restore, staged load tests, payments, multi-provider AI orchestration, OAuth/webhooks and the additional business domains remain unverified or unimplemented. A CI pass is not production approval.
