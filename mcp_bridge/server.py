"""Read-only MCP bridge for the public Global AI Platform repository."""
from __future__ import annotations
import base64
import os
import re
from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

REPOSITORY = os.getenv("GITHUB_REPOSITORY", "hijaz7861/global-ai-platform")
PATH_TOKEN = os.getenv("MCP_PATH_TOKEN", "")
if len(PATH_TOKEN) < 40:
    raise RuntimeError("MCP_PATH_TOKEN must contain at least 40 characters.")
OWNER, SEP, REPO = REPOSITORY.partition("/")
if not SEP or not OWNER or not REPO or "/" in REPO:
    raise RuntimeError("GITHUB_REPOSITORY must use owner/repository format.")
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "global-ai-platform-mcp-bridge",
}
MAX_FILE_BYTES = 40_000
SECRET_PATH = re.compile(
    r"(^|/)(\.env($|\.)|secrets?(/|$)|credentials?(/|$)|"
    r"id_rsa($|\.)|.*\.(pem|key|p12|pfx)$)",
    re.IGNORECASE,
)

def _safe_path(path: str) -> str:
    value = path.strip().replace("\\", "/")
    if not value or value.startswith("/") or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ValueError("A safe repository-relative file path is required.")
    if SECRET_PATH.search(value):
        raise ValueError("Access to secret-like paths is blocked.")
    return value

async def _get(client: httpx.AsyncClient, url: str, **params: Any) -> Any:
    response = await client.get(url, params=params or None)
    if response.status_code == 404:
        raise ValueError("GitHub resource was not found.")
    if response.status_code == 403:
        raise RuntimeError("GitHub API rate limit or access restriction; retry later.")
    response.raise_for_status()
    return response.json()

mcp = FastMCP(
    "Global AI Platform Repository Bridge",
    instructions=(
        "Read-only tools for hijaz7861/global-ai-platform. Inspect metadata, files, "
        "commits and CI runs. This server cannot modify files, commit, push, deploy, "
        "dispatch workflows, or execute shell commands."
    ),
    host="0.0.0.0",
    streamable_http_path=f"/{PATH_TOKEN}/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

@mcp.tool()
async def repository_status() -> dict[str, Any]:
    """Get repository visibility, default branch and latest commit."""
    async with httpx.AsyncClient(headers=HEADERS, timeout=20.0) as client:
        repo_data = await _get(client, API)
        commits = await _get(client, f"{API}/commits", per_page=1)
    latest = commits[0] if commits else {}
    return {
        "repository": REPOSITORY,
        "visibility": repo_data.get("visibility"),
        "default_branch": repo_data.get("default_branch"),
        "archived": repo_data.get("archived"),
        "updated_at": repo_data.get("updated_at"),
        "latest_commit": latest.get("sha"),
        "latest_commit_message": latest.get("commit", {}).get("message", "")[:300],
    }

@mcp.tool()
async def list_repository_files(path_prefix: str = "", max_results: int = 100) -> dict[str, Any]:
    """List tracked repository files, optionally filtered by directory; omit secret-like paths."""
    limit = max(1, min(int(max_results), 300))
    prefix = path_prefix.strip().strip("/")
    if prefix:
        _safe_path(prefix + "/placeholder")
    async with httpx.AsyncClient(headers=HEADERS, timeout=30.0) as client:
        repo_data = await _get(client, API)
        branch = repo_data["default_branch"]
        tree = await _get(client, f"{API}/git/trees/{branch}", recursive="1")
    files = [
        item["path"] for item in tree.get("tree", [])
        if item.get("type") == "blob"
        and (not prefix or item["path"].startswith(prefix + "/") or item["path"] == prefix)
        and not SECRET_PATH.search(item["path"])
    ]
    return {
        "repository": REPOSITORY,
        "branch": branch,
        "truncated": bool(tree.get("truncated")),
        "count_returned": min(len(files), limit),
        "files": files[:limit],
    }

@mcp.tool()
async def read_repository_file(path: str) -> dict[str, Any]:
    """Read one tracked UTF-8 text file up to 40 KB; secret-like paths and binary files are blocked."""
    safe = _safe_path(path)
    async with httpx.AsyncClient(headers=HEADERS, timeout=20.0) as client:
        data = await _get(client, f"{API}/contents/{safe}")
    if data.get("type") != "file":
        raise ValueError("The requested path is not a file.")
    if int(data.get("size", 0)) > MAX_FILE_BYTES:
        raise ValueError(f"File exceeds the {MAX_FILE_BYTES}-byte read limit.")
    if data.get("encoding") != "base64" or not data.get("content"):
        raise ValueError("GitHub did not return a supported text-file payload.")
    raw = base64.b64decode(data["content"])
    if b"\x00" in raw:
        raise ValueError("Binary files are not supported.")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("File is not valid UTF-8 text.") from exc
    return {"repository": REPOSITORY, "path": safe, "size_bytes": len(raw),
            "content": content, "html_url": data.get("html_url")}

@mcp.tool()
async def list_recent_commits(limit: int = 10, branch: str = "") -> list[dict[str, Any]]:
    """List recent commits with SHA, message, author and timestamp."""
    params: dict[str, Any] = {"per_page": max(1, min(int(limit), 20))}
    if branch.strip():
        params["sha"] = branch.strip()
    async with httpx.AsyncClient(headers=HEADERS, timeout=20.0) as client:
        commits = await _get(client, f"{API}/commits", **params)
    return [
        {"sha": item.get("sha"), "message": item.get("commit", {}).get("message", "")[:300],
         "author": item.get("commit", {}).get("author", {}).get("name"),
         "date": item.get("commit", {}).get("author", {}).get("date"),
         "url": item.get("html_url")}
        for item in commits
    ]

@mcp.tool()
async def list_ci_runs(limit: int = 10, branch: str = "") -> dict[str, Any]:
    """List recent GitHub Actions runs; does not start or rerun workflows."""
    params: dict[str, Any] = {"per_page": max(1, min(int(limit), 20))}
    if branch.strip():
        params["branch"] = branch.strip()
    async with httpx.AsyncClient(headers=HEADERS, timeout=20.0) as client:
        data = await _get(client, f"{API}/actions/runs", **params)
    return {
        "repository": REPOSITORY, "total_count": data.get("total_count", 0),
        "runs": [
            {"name": item.get("name"), "status": item.get("status"),
             "conclusion": item.get("conclusion"), "branch": item.get("head_branch"),
             "commit": item.get("head_sha"), "created_at": item.get("created_at"),
             "html_url": item.get("html_url")}
            for item in data.get("workflow_runs", [])
        ],
    }

async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "global-ai-platform-mcp-bridge"})

app = mcp.streamable_http_app()
app.router.routes.insert(0, Route("/health", health, methods=["GET"]))
