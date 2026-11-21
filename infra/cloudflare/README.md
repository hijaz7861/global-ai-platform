# HIJAZ AI — Cloudflare Free Edge Infrastructure

This directory contains the source for the small public edge/status service deployed as the Cloudflare Worker `hijaz-ai-edge`.

## Live resources (Cloudflare account)

- Worker: `hijaz-ai-edge`
- workers.dev hostname: `hijaz-ai-edge.hijaz-ai-platform.workers.dev`
- D1 database: `hijaz-ai-core`
- KV namespace: `HIJAZ_AI_CONFIG`
- D1 tables: `platform_meta`, `audit_events`, `agent_tasks` (+ SQLite internal metadata)
- Worker bindings: `DB` (D1), `CONFIG` (KV)

## Endpoints

- `GET /` — service identity and endpoint index
- `GET /health` and `GET /api/v1/health` — D1 connectivity/schema health
- `GET /api/v1/status` — platform metadata and task/audit counters
- Other paths return JSON 404. Non-GET/HEAD methods return 405.

The public status API is intentionally read-only. No public endpoint accepts writes, executes arbitrary SQL, creates tasks, or exposes credentials.

## Architecture boundary

This Worker is an edge health/status façade and a first deployment target. It does **not** replace the existing FastAPI/PostgreSQL application, tenant authorization, durable agent runner, or signed human-approval workflow. The main application remains the system of record. D1 currently stores only edge/platform metadata, a minimal audit record, and the future task-table scaffold.

The KV binding is provisioned for non-sensitive configuration/cache use. Do not store API keys, passwords, signing keys, refresh tokens, or other secrets in KV.

## Free-tier posture

The resources were provisioned without upgrading the Cloudflare plan. Keep within current Free quotas; do not enable paid billing, paid-only Vectorize, Containers, or paid-only models as part of this deployment. D1 Free over-quota writes can fail until the daily reset. Workers Free has strict CPU/request limits, so this Worker must remain lightweight.

## Reproducibility

The deployed Worker source is `worker.js`. Its Cloudflare bindings are account-specific and are configured outside this source file. Keep database IDs and namespace IDs in deployment configuration, not hard-coded into application source.

For a fresh account, create a D1 database and KV namespace, apply the schema described in `docs/CLOUDFLARE_FREE_INFRASTRUCTURE.md`, upload this Worker as an ES module with `DB` and `CONFIG` bindings, create/choose a `workers.dev` subdomain, and enable the Worker subdomain route.
