# Global AI Platform — Hosted MCP Bridge

Read-only MCP Streamable HTTP service for `hijaz7861/global-ai-platform`.

## Tools
- `repository_status` — repository metadata and latest commit.
- `list_repository_files` — tracked file paths; secret-like paths excluded.
- `read_repository_file` — UTF-8 text files up to 40 KB.
- `list_recent_commits` — recent commit history.
- `list_ci_runs` — recent GitHub Actions runs, read-only.

No GitHub credential is stored. No file writes, commits, pushes, arbitrary shell execution,
workflow dispatch, or deployment operations are exposed.

## Render
Build: `pip install -r mcp_bridge/requirements.txt`
Start: `cd mcp_bridge && uvicorn server:app --host 0.0.0.0 --port $PORT`

Required environment:
- `GITHUB_REPOSITORY=hijaz7861/global-ai-platform`
- `MCP_PATH_TOKEN`: high-entropy value (at least 40 characters).

MCP endpoint: `https://YOUR-RENDER-HOST/<MCP_PATH_TOKEN>/mcp`
Health endpoint: `/health`

The path token is a bearer-equivalent secret; rotate it if exposed. Render Free services
may spin down while idle, so the first request after inactivity may be delayed.
