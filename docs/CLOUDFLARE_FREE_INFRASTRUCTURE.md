# HIJAZ AI — Cloudflare Free Infrastructure

## Deployment record

Provisioned on 2026-11-19 in the connected Cloudflare account without a plan upgrade.

| Resource | Name | State |
|---|---|---|
| Workers subdomain | `hijaz-ai-platform` | Created |
| Worker | `hijaz-ai-edge` | Uploaded; deployment record created; workers.dev enabled |
| D1 | `hijaz-ai-core` | Created; schema applied |
| KV | `HIJAZ_AI_CONFIG` | Created; bound to Worker |
| R2 | — | Not enabled; account previously returned error 10042 |
| Custom domain/zone | — | None present during initial inspection |

## D1 schema

The database contains:

- `platform_meta(key, value, updated_at)` — platform metadata and schema version.
- `audit_events(id, event_type, actor, payload_json, created_at)` — append-oriented event history.
- `agent_tasks(id, status, task_type, input_json, result_json, created_at, updated_at)` — task scaffold with constrained lifecycle status.

Indexes exist for audit creation time and task status. One `infrastructure.initialized` audit event was written during provisioning.

## Public edge API

Base URL: `https://hijaz-ai-edge.hijaz-ai-platform.workers.dev`

- `GET /` — service identity and endpoint list
- `GET /health` and `GET /api/v1/health` — D1 connectivity and schema health
- `GET /api/v1/status` — platform metadata and audit/task counters

The Worker uses prepared D1 statements and returns non-cacheable JSON with basic browser security headers. It is read-only by design.

## Verification

Verified on 2026-11-19:

- Worker upload returned `success: true` and version ID `104bc791-8660-469a-a2f8-7f936edf2430`.
- Cloudflare deployment list shows version 1 assigned 100%.
- Worker `workers.dev` route is enabled.
- Worker settings show both the D1 and KV bindings.
- D1 SQL API verified the application tables, one initialization audit event, and zero agent tasks.
- Live browser request to `/api/v1/health` returned `200` with `ok: true`, `database: connected`, and schema version `1`.
- Live browser request to `/api/v1/status` returned `200`, showing D1 and KV as configured and the expected metadata/counters.
- `node --check` passed for the Worker module syntax.

## Important boundary

This deployment does not host the full FastAPI application or a Qwen3-8B model. Cloudflare Workers is not a free GPU host. The existing Global AI Platform remains the authoritative backend and needs a separate compute/runtime deployment. The Worker is a lightweight public edge service and metadata store only.

## Security follow-up

Before exposing authenticated business or AI operations, add a proper authentication boundary (for example, validated JWTs or Cloudflare Access for a private admin surface), tenant authorization, request schemas, rate limiting, and audit coverage. Never add a generic SQL execution endpoint or unauthenticated write endpoint. Secrets belong in Worker Secrets, not source control or KV.
