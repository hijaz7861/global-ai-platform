# Global AI-Powered Business Ecosystem

A private, modular project for building a global business platform incrementally. The current draft contains a FastAPI API, identity and organization membership endpoints, a tenant-scoped catalog, a basic order workflow with stock reservation and cancellation, a double-entry ledger foundation, audit events, and a guarded OpenAI-compatible AI adapter. It is not yet a complete business ecosystem.

## Current foundation

- FastAPI application with OpenAPI docs.
- Versioned process health endpoint: `GET /api/v1/health`.
- Registration with Argon2 password hashing, signed short-lived bearer access tokens, hashed rotating refresh tokens, refresh replay detection, session-family revocation, and server-side logout revocation.
- Email-based password reset with hashed, expiring, single-use tokens; successful reset invalidates existing sessions.
- Organization creation, membership-filtered access, and owner/admin member management.
- Tenant-scoped catalog listings with owner/admin publication, plus public search, currency and price filters, and bounded pagination.
- Organization-scoped supplier onboarding with draft/submitted/approved/rejected states and platform-admin review.
- Owner/admin inventory adjustments with nonnegative stock checks, append-only before/after history, optional reason, and transaction-bound audit events.
- Order creation with conditional stock reservation and per-buyer idempotency.
- Buyer-only order reads, seller-scoped order summaries, and unpaid-order cancellation with one-time stock release.
- Owner/admin chart of accounts, balanced idempotent journal posting, and a derived trial balance.
- Transaction-bound allowlisted audit events for organization, catalog, order, and ledger operations.
- Guarded OpenAI-compatible AI chat adapter; external calls are disabled by default, require explicit opt-in, a positive request limit, and PostgreSQL.
- Orders remain `pending_payment`; no payment is charged or processed.
- SQLAlchemy models and Alembic migrations for identity, rotating refresh-token families, tenants, catalog, inventory adjustment history, orders, audit, accounting periods, journal entries and reversals, token revocation, password reset, and supplier profiles.
- GitHub Actions CI on Python 3.11, 3.12, and 3.13, including migration checks.
- Optional Docker packaging that applies migrations before starting the API.

## Run locally

Python 3.11 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m alembic upgrade head
python -m compileall -q app tests migrations
python -m pytest -q
uvicorn app.main:app --reload
```

Set a unique random `SECRET_KEY` of at least 32 characters in `.env` before using login. `REFRESH_TOKEN_EXPIRE_DAYS` controls refresh-session lifetime (1–90 days); clients must replace refresh tokens after each successful `/api/v1/auth/refresh` response and discard the old token. Configure `SMTP_HOST`, `SMTP_FROM_EMAIL`, and SMTP credentials to deliver password-reset email; reset requests use the same response for known and unknown accounts. Set `PLATFORM_ADMIN_EMAILS` to a comma-separated allowlist for supplier review; leave it empty to disable platform-admin review access. Live AI calls stay disabled unless deliberately enabled in configuration. For PostgreSQL deployments, use `DATABASE_URL=postgresql+psycopg://...` and install `python -m pip install 'psycopg[binary]>=3.2,<4'`.

Open `http://127.0.0.1:8000/docs` or request `http://127.0.0.1:8000/api/v1/health`.

## Core product requirements

- Global B2B and B2C marketplace.
- Supplier, manufacturer, wholesale and retail management.
- Logistics, freight, vehicle and equipment rentals.
- Real estate listings, rentals and sales.
- Employment, contractors, freelancers and services.
- Inventory, store management, marketing and education.
- Secure Universal Connector Hub for authorized APIs, OAuth and webhooks.
- Multi-provider AI orchestration and specialized AI agents.
- AI-powered accounting, double-entry ledger, invoices, commissions and reconciliation.
- Controlled AI research and experimentation laboratory.
- What-if simulations and business analytics.
- Strict tenant isolation, least privilege, audit logs and encrypted secrets.
- Web-first architecture with future mobile and desktop expansion.

## Engineering rules

- Never claim a test passed unless it was executed and verified.
- Never commit API keys, passwords, tokens or `.env` files.
- Require authorization before paid services or production deployment.
- Use modular architecture and implement incrementally.
- Require human approval for sensitive financial and irreversible actions.
- Verify external integrations and permissions before relying on them.
- Documentation and scaffolding are not proof that a feature is implemented.

## Roadmap and limitations

Refresh-token behavior: login returns a short-lived access token plus a random refresh token whose SHA-256 digest is stored server-side. `POST /api/v1/auth/refresh` rotates the refresh token; clients must persist the newly returned token and discard the old one. Reuse of a consumed token revokes the session family and invalidates that family’s access tokens. Logout revokes the current access token and its refresh-token family. Deploy behind HTTPS and never log or expose tokens.

See [Master Blueprint](docs/MASTER_BLUEPRINT.md), [Product Requirements](docs/requirements.md), [Identity and Tenancy](docs/IDENTITY_TENANCY.md), [Catalog](docs/CATALOG.md), [Orders](docs/ORDERS.md), [Accounting](docs/ACCOUNTING.md), [AI Orchestration](docs/AI_ORCHESTRATION.md), [Architecture](docs/ARCHITECTURE.md), [Security](docs/SECURITY.md), and [Roadmap](docs/ROADMAP.md).

MFA, stronger concurrency-safe throttling, invitations, expanded role administration, payment processing, period-specific financial reporting, reconciliation, provider routing/fallback, native provider integrations, agent tools, external connectors, supplier procurement/quotes, logistics, rentals, real estate, workforce, and education remain future work. Do not expose this foundation publicly as a production service.
