"""Short-lived, repository-scoped GitHub App installation token provider."""
from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timezone
import re
import threading
import time
import httpx
import jwt


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


class GitHubAppAuthError(RuntimeError):
    """A sanitized GitHub App authentication/configuration failure."""


class GitHubAppTokenProvider:
    """Mint and cache short-lived installation tokens restricted to one repo."""

    def __init__(
        self,
        *,
        app_id: str,
        installation_id: str,
        private_key_pem: str,
        allowed_repositories: Collection[str],
        client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(app_id, str) or not app_id.isdigit() or len(app_id) > 32:
            raise ValueError("A numeric GitHub App ID is required")
        if not isinstance(installation_id, str) or not installation_id.isdigit() or len(installation_id) > 32:
            raise ValueError("A numeric GitHub installation ID is required")
        if (
            not isinstance(private_key_pem, str)
            or "-----BEGIN" not in private_key_pem
            or "PRIVATE KEY-----" not in private_key_pem
        ):
            raise ValueError("A PEM-encoded GitHub App private key is required")
        if any(not isinstance(item, str) for item in allowed_repositories):
            raise ValueError("Repository allowlist entries must be strings")
        allowed = frozenset(item.strip() for item in allowed_repositories)
        if not allowed or any(not _REPOSITORY.fullmatch(item) for item in allowed):
            raise ValueError("An explicit owner/repository allowlist is required")

        self._app_id = app_id
        self._installation_id = installation_id
        self._private_key_pem = private_key_pem
        self._allowed = allowed
        self._owns_client = client is None
        if client is not None and str(client.base_url).rstrip("/") != "https://api.github.com":
            raise ValueError("GitHub App client must use the official api.github.com origin")
        self._client = client or httpx.Client(
            base_url="https://api.github.com",
            timeout=httpx.Timeout(15.0),
            follow_redirects=False,
        )
        self._client.follow_redirects = False
        self._client.timeout = httpx.Timeout(15.0)
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _app_jwt(self, now: int) -> str:
        claims = {"iat": now - 60, "exp": now + 540, "iss": self._app_id}
        try:
            return jwt.encode(claims, self._private_key_pem, algorithm="RS256")
        except (jwt.PyJWTError, ValueError, TypeError):
            raise GitHubAppAuthError("github_app_private_key_invalid") from None

    def get_token(self, repository: str) -> str:
        """Return a short-lived installation token scoped to the named repository."""
        if repository not in self._allowed or not _REPOSITORY.fullmatch(repository):
            raise GitHubAppAuthError("repository_not_allowlisted")
        now = time.time()
        with self._lock:
            cached = self._cache.get(repository)
            if cached is not None and cached[1] > now + 60:
                return cached[0]

            _, repo_name = repository.split("/", 1)
            path = f"/app/installations/{self._installation_id}/access_tokens"
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._app_jwt(int(now))}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "global-ai-platform-app-token-provider",
            }
            payload = {
                "repositories": [repo_name],
                "permissions": {
                    "contents": "write",
                    "pull_requests": "write",
                },
            }
            try:
                response = self._client.post(path, json=payload, headers=headers)
            except httpx.HTTPError:
                raise GitHubAppAuthError("github_app_token_transport_error") from None
            if not 200 <= response.status_code < 300:
                raise GitHubAppAuthError(f"github_app_token_http_{response.status_code}")
            try:
                data = response.json()
            except ValueError:
                raise GitHubAppAuthError("github_app_token_invalid_response") from None
            token = data.get("token") if isinstance(data, dict) else None
            expires_at = data.get("expires_at") if isinstance(data, dict) else None
            if not isinstance(token, str) or len(token) < 20 or not isinstance(expires_at, str):
                raise GitHubAppAuthError("github_app_token_invalid_response")
            try:
                expiry_time = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expiry_time.tzinfo is None:
                    raise ValueError("timezone_required")
                expiry = expiry_time.timestamp()
            except ValueError:
                raise GitHubAppAuthError("github_app_token_invalid_expiry") from None
            if expiry <= now + 60:
                raise GitHubAppAuthError("github_app_token_expired")
            self._cache[repository] = (token, expiry)
            return token
