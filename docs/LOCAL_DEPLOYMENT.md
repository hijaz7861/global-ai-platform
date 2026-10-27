# Local and private-host deployment

This is a reproducible Docker Compose deployment path for the Global AI Platform core. It runs the API and PostgreSQL 16, waits for the database health check, applies Alembic migrations through the Docker entrypoint, and checks the API's database readiness endpoint.

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
docker compose logs --tail=100 api
curl --fail http://127.0.0.1:8000/api/v1/health
curl --fail http://127.0.0.1:8000/api/v1/ready
```

Expected readiness response: {"status":"ready"}. Check migration output in the API logs before creating accounts. The API binds to loopback by default; it is not exposed directly to the public internet.

## 4. Operating rules

- Keep the database port private; Compose does not publish it.
- Keep AI live calls disabled until provider permissions, a spending limit, and an approval policy are reviewed.
- Put a maintained TLS reverse proxy in front of the API before remote access. If using a proxy, configure TRUSTED_PROXY_IPS with only the proxy's exact IP/CIDR.
- Configure encrypted backups and prove a restore before production use.
- Schedule python -m app.maintenance using a trusted external scheduler and monitor its result.
- Set up log collection, alerts, database resource monitoring, and an incident/rollback procedure.
- To stop: docker compose down. To remove the database volume as well, docker compose down -v permanently deletes local database data; do not run this unless deletion is intended.

This provides a private-host deployment baseline, not a production certification. SMTP, backups, TLS, monitoring, access policy, and full current-head CI still require operator verification.
