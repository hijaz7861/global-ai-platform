# Local and private-host deployment

This is a reproducible Docker Compose deployment path for the HIJAZ ONE portable core. It runs the API, durable AI task worker, database-coordinated maintenance scheduler, and PostgreSQL 16. Worker and scheduler start only after the API is healthy (and therefore after its migration entrypoint has completed). API and worker share PostgreSQL and AI configuration; the scheduler uses a PostgreSQL advisory lock and the committed maintenance audit event as its durable checkpoint. SQLite is limited to local single-process scheduler use.

## 1. Prepare secrets

Install Docker Engine with the Compose plugin. From the repository root, create an untracked .env file. Generate secrets rather than reusing example values:

```sh
umask 077
printf 'SECRET_KEY=%s\n' "$(openssl rand -hex 32)" > .env
printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 32)" >> .env
```

The database password is URL-safe hexadecimal because it is also embedded in the PostgreSQL URL. Never commit .env.

## 2. Configure email and platform administrators

Edit .env and add the real SMTP values needed for registration verification, password reset, and invitations. Also set an explicit administrator allowlist:

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=replace-me
SMTP_PASSWORD=replace-me
SMTP_FROM_EMAIL=no-reply@example.com
SMTP_STARTTLS=true
PLATFORM_ADMIN_EMAILS=admin@example.com
```

These are placeholders, not working credentials. Do not expose the service to real users until SMTP delivery and admin authorization have been tested.

## 3. Start and verify

```sh
docker compose config
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 api worker scheduler
curl --fail http://127.0.0.1:8000/api/v1/health
curl --fail http://127.0.0.1:8000/api/v1/ready
docker compose ps worker scheduler
```

Expected readiness response: {"status":"ready"}. Check migration output in the API logs before creating accounts. The API binds to loopback by default; it is not exposed directly to the public internet.

## 4. Operating rules

- Keep the database port private; Compose does not publish it.
- AI execution is disabled by default. To enable agent tasks, configure `AI_LIVE_CALLS_ENABLED=true`, a positive `AI_DAILY_REQUEST_LIMIT`, and the selected provider's credentials/model in `.env` before recreating both API and worker. This explicitly permits external model calls and possible charges; keep output-token limits low.
- The worker continuously polls PostgreSQL for durable tasks, renews task leases through the task runtime, and restarts under Docker's `unless-stopped` policy after an unexpected process exit. Inspect `docker compose logs -f worker` and `docker compose ps` to monitor it. The worker only runs registered task handlers; it does not provide shell, arbitrary network, write, payment, or deployment tools.
- Put a maintained TLS reverse proxy in front of the API before remote access. If using a proxy, configure TRUSTED_PROXY_IPS with only the proxy's exact IP/CIDR.
- Configure encrypted backups and prove a restore before production use.
- The separate Compose scheduler runs bounded maintenance at the configured interval. It uses a PostgreSQL advisory lock to prevent concurrent replicas and coalesces missed intervals into one recovery run. See [Maintenance Scheduler](MAINTENANCE_SCHEDULER.md); do not also schedule the same database with external cron.
- Set up log collection, alerts, database resource monitoring, and an incident/rollback procedure.
- To restart the worker or scheduler after configuration changes: `docker compose up -d --force-recreate worker scheduler api`.
- To stop: docker compose down. To remove the database volume as well, docker compose down -v permanently deletes local database data; do not run this unless deletion is intended.

This provides a private-host deployment baseline, not a production certification. SMTP, backups, TLS, monitoring, access policy, and full current-head CI still require operator verification.
