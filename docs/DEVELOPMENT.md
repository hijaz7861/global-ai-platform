# Development Workflow

## Local setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m compileall -q app tests
python -m pytest -q
uvicorn app.main:app --reload
```

Open `/docs` for API documentation and `/api/v1/health` for the process health endpoint.

## Change policy
1. Inspect the existing implementation.
2. Reproduce the failure or define acceptance criteria.
3. Make the smallest coherent change.
4. Run targeted tests and the full regression suite.
5. Record actual results, including failures.
6. Keep secrets and local environments out of Git.
7. Review changes before merging or deployment.
