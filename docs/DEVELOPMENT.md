# Development Workflow

## Local setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m compileall -q app tests migrations
ruff check app tests migrations
python -m alembic upgrade head
python -m alembic check
python -m pytest -q
uvicorn app.main:app --reload
```

Open `/docs` for API documentation and `/api/v1/health` for the process health endpoint.

## PostgreSQL and AI
SQLite is the default development database. Live AI calls are intentionally disabled by default and require PostgreSQL row locking, a configured OpenAI-compatible endpoint, an explicit opt-in, and a positive rolling 24-hour request limit. Install the PostgreSQL driver separately with `python -m pip install 'psycopg[binary]>=3.2,<4'` where supported.

## Change policy
1. Inspect the existing implementation.
2. Reproduce the failure or define acceptance criteria.
3. Make the smallest coherent change.
4. Run targeted tests and the full regression suite.
5. Record actual results, including failures.
6. Keep secrets and local environments out of Git.
7. Review changes before merging or deployment.
