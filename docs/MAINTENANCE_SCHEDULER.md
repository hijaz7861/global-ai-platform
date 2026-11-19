# Durable maintenance scheduler

The portable runtime includes a separate scheduler process. It runs the existing app.maintenance.run_maintenance_once batch for unpaid-order expiry, expired security-record cleanup, and explicitly expired AI-memory purge.

## Coordination and durable checkpoint

- The last committed platform.maintenance_completed row in audit_events is the durable last-success checkpoint. The maintenance operations and this audit event are committed in the same database transaction.
- PostgreSQL deployments use a session-level advisory lock derived from the fixed scheduler lock name. Only one scheduler replica can run a maintenance batch at a time. The lock connection is held for the entire batch and explicitly unlocked before returning it to the connection pool.
- SQLite is supported only for local single-process development and uses a process-local lock. Production configuration already requires PostgreSQL; do not run multiple SQLite scheduler processes.
- On startup, no previous checkpoint means one immediate bounded run. An overdue checkpoint triggers one recovery run; missed intervals are coalesced instead of generating a catch-up burst. A successful run moves the checkpoint forward through the existing audit event.
- If the maintenance transaction fails, its completion event is rolled back and the scheduler retries on a later poll. If the process exits after commit, the durable audit checkpoint prevents an unnecessary duplicate run.
- Scheduler output contains counts only. It does not print expired order IDs, memory content, credentials, or raw database exception text.

This provides single-runner coordination and missed-run recovery; it is not a general-purpose arbitrary cron engine. The schedule is fixed by operator configuration, with no user-facing job creation or arbitrary task execution.

## Configuration

| Variable | Default | Bound |
|---|---:|---:|
| MAINTENANCE_INTERVAL_SECONDS | 3600 | 60–86400 |
| MAINTENANCE_POLL_SECONDS | 30 | 1–300 |
| MAINTENANCE_ORDER_LIMIT | 100 | 1–500 |

Run a single due check manually:

~~~sh
python -m app.maintenance_scheduler --once
~~~

The Docker Compose scheduler service starts after the API health check; the API image entrypoint applies Alembic migrations before becoming healthy. Monitor it with:

~~~sh
docker compose logs -f scheduler
docker compose ps scheduler
~~~

The original python -m app.maintenance command remains available as a one-shot operator command. Do not configure both an external cron and the Compose scheduler for the same database unless duplicate overlapping maintenance runs are explicitly intended.
