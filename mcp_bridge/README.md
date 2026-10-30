# Global AI Platform — Hosted MCP Bridge

A read-only MCP Streamable HTTP service for the public repository
`hijaz7861/global-ai-platform`.

## Tools
- `repository_status` — repository metadata and latest commit.
- `list_repository_files` — tracked file paths; secret-like paths excluded.
- `read_repository_file` — UTF-8 text files up to 40 KB.
- `list_recent_commits` — recent commit history.
- `list_ci_runs` — recent GitHub Actions runs, read-only.

No GitHub credential is stored. No file writes, commits, pushes, arbitrary shell execution,
workflow dispatch, or deployment operations are exposed. Because the source repository is
public, this hosted MCP endpoint is also public and read-only.

## Render
Build: `pip install -r mcp_bridge/requirements.txt`
Start: `cd mcp_bridge && uvicorn server:app --host 0.0.0.0 --port $PORT`

Environment:
- `GITHUB_REPOSITORY=hijaz7861/global-ai-platform`

MCP endpoint: `https://YOUR-RENDER-HOST/mcp`
Health endpoint: `/health`

Render Free services may spin down while idle, so the first request after inactivity may
be delayed. The bridge uses GitHub's unauthenticated API rate limits.
