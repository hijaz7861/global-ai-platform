# Global AI Platform — Hosted MCP Bridge

A read-only MCP Streamable HTTP service for the public repository
`hijaz7861/global-ai-platform`.

## MCP tools

- `repository_status` — repository visibility, default branch and latest commit.
- `list_repository_files` — tracked file paths; secret-like paths are excluded.
- `read_repository_file` — UTF-8 text files up to 40 KB; secret-like and binary files are blocked.
- `list_recent_commits` — recent commit history.
- `list_ci_runs` — recent GitHub Actions runs; workflows are never started or rerun.

The bridge is read-only. It has no GitHub credential and exposes no file writes, commits,
pushes, arbitrary shell execution, workflow dispatch, or deployment operations.

## Authentication and endpoints

Set `MCP_PATH_TOKEN` in the hosting provider's environment configuration. It must be
at least 40 characters. Treat it as a secret: do not commit it, publish it, or include it
in logs or screenshots.

The MCP endpoint is:

```text
https://YOUR-HOST/<MCP_PATH_TOKEN>/mcp
```

The health endpoint is:

```text
https://YOUR-HOST/health
```

The health endpoint does not require the path token. The MCP endpoint is mounted beneath
the tokenized path and is not available at `/mcp` directly. Clients must use the complete
tokenized MCP URL. Keep Vercel Deployment Protection enabled; it is an additional access
control layer and is separate from the MCP path token.

## Vercel deployment

The Vercel project root directory is `mcp_bridge`. Its Python Function entrypoint is
`api/index.py`, which exports the Starlette application from `server.py`.

Required environment variables:

- `MCP_PATH_TOKEN` — random secret, at least 40 characters.
- `GITHUB_REPOSITORY=hijaz7861/global-ai-platform` — optional; this is the default.

Dependencies are installed from `requirements.txt`.

## Local development

```bash
pip install -r requirements.txt
export MCP_PATH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
uvicorn server:app --host 127.0.0.1 --port 8000
```

Use `http://127.0.0.1:8000/<MCP_PATH_TOKEN>/mcp` as the local MCP URL.

## Operational notes

The repository is public, so the bridge uses GitHub's unauthenticated API. GitHub API
rate limits apply. Never expose the path token in client-visible logs, URLs posted publicly,
or support screenshots.
