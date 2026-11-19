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

- `GET /health`
- `GET /api/v1/health`
- `GET /api/v1/status`

The Worker uses prepared D1 statements and returns non-cacheable JSON with basic browser security headers. It is read-only by design.

## Verification and limitations

Verified through Cloudflare control-plane reads:

- Worker upload returned `success: true` and a version/deployment identifier.
- Worker deployment list shows version 1 assigned 100%.
- Worker subdomain route is enabled.
- Worker settings show both the D1 and KV bindings.
- D1 query verified four tables including the three application tables plus one internal SQLite table, one audit event, and zero agent tasks.

Live HTTP verification through the available external fetch clients returned `target_unreachable` / inaccessible URL. Therefore, DNS/edge HTTP reachability and actual endpoint response are **not yet independently verified**, despite the successful Cloudflare control-plane deployment state. Re-test the public URL after propagation before declaring production readiness.

## Important boundary

This deployment does not host the full FastAPI application or a Qwen3-8B model. Cloudflare Workers is not a free GPU host. The existing Global AI Platform remains the authoritative backend and needs a separate compute/runtime deployment. The Worker is a lightweight public edge service and metadata store only.

## Security follow-up

Before exposing authenticated business or AI operations, add a proper authentication boundary (for example, validated JWTs or Cloudflare Access for a private admin surface), tenant authorization, request schemas, rate limiting, and audit coverage. Never add a generic SQL execution endpoint or unauthenticated write endpoint. Secrets belong in Worker Secrets, not source control or KV.
