# Initial architecture

Start with a modular monolith to reduce cost and operational complexity.

- API: FastAPI
- Database: PostgreSQL when database integration is introduced
- Tests: pytest
- Configuration: environment variables; secrets never committed
- Domain modules: commerce, suppliers, logistics, rentals, property,
  jobs, education, accounting, AI orchestration and connectors
- Security: least privilege, tenant-scoped data access, audit trails
- Integrations: adapters with explicit permissions, timeouts and budgets

This is an initial design. Database, authentication, billing, AI providers,
and external integrations are not yet implemented.
