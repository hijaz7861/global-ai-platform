# Global AI-Powered Business Ecosystem

A private, modular project for building a global business platform incrementally. This repository currently contains a runnable API foundation and a documented roadmap; it is not yet a complete business ecosystem.

## Current foundation

- FastAPI application with OpenAPI docs.
- Versioned process health endpoint: `GET /api/v1/health`.
- Service identity endpoint: `GET /`.
- Environment-backed settings.
- HTTP contract tests with pytest.
- GitHub Actions CI on Python 3.11, 3.12, and 3.13.
- Optional Docker packaging.

## Run locally

Python 3.11 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m compileall -q app tests
python -m pytest -q
uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000/docs` or request `http://127.0.0.1:8000/api/v1/health`.

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
- Low-cost, open-source-first development.

## Engineering rules

- Never claim a test passed unless it was executed and verified.
- Never commit API keys, passwords, tokens or `.env` files.
- Require authorization before paid services or production deployment.
- Use modular architecture and implement incrementally.
- Require human approval for sensitive financial and irreversible actions.
- Verify external integrations and permissions before relying on them.
- Documentation and scaffolding are not proof that a feature is implemented.

## Roadmap

See [Master Blueprint](docs/MASTER_BLUEPRINT.md), [Product Requirements](docs/requirements.md), [Architecture](docs/ARCHITECTURE.md), [Security](docs/SECURITY.md), and [Roadmap](docs/ROADMAP.md).

Authentication, tenant isolation, database persistence, billing, marketplace workflows, AI orchestration, and third-party connectors are not implemented yet. Do not expose the current unauthenticated API publicly as a production service.
