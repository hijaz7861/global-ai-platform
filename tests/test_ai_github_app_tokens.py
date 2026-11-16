from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.ai.github_app_tokens import GitHubAppAuthError, GitHubAppTokenProvider


def _key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_key = private_key.public_key()
    return private_pem, public_key


def _provider(handler, private_pem, public_key, *, repositories=("acme/platform",)):
    client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )
    provider = GitHubAppTokenProvider(
        app_id="12345",
        installation_id="67890",
        private_key_pem=private_pem,
        allowed_repositories=set(repositories),
        client=client,
    )
    return provider, client, public_key


def test_provider_mints_repo_scoped_token_and_caches_until_near_expiry():
    private_pem, public_key = _key_pair()
    requests = []
    token_counter = {"value": 0}

    def handler(request):
        requests.append(request)
        assert request.url.path == "/app/installations/67890/access_tokens"
        assert request.headers["Authorization"].startswith("Bearer ")
        app_jwt = request.headers["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(app_jwt, public_key, algorithms=["RS256"])
        assert claims["iss"] == "12345"
        body = request.read()
        import json
        payload = json.loads(body)
        assert payload["repositories"] == ["platform"]
        assert payload["permissions"] == {"contents": "write", "pull_requests": "write"}
        token_counter["value"] += 1
        return httpx.Response(
            201,
            json={
                "token": f"ghs_test_installation_token_{token_counter['value']:02d}",
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
            },
        )

    provider, client, _ = _provider(handler, private_pem, public_key)
    try:
        first = provider.get_token("acme/platform")
        second = provider.get_token("acme/platform")
        assert first == second
        assert len(requests) == 1
    finally:
        provider.close()
        client.close()


def test_provider_rejects_repository_outside_allowlist_before_network():
    private_pem, public_key = _key_pair()
    requests = []
    provider, client, _ = _provider(
        lambda request: (requests.append(request), httpx.Response(201, json={}))[1],
        private_pem,
        public_key,
    )
    try:
        with pytest.raises(GitHubAppAuthError, match="repository_not_allowlisted"):
            provider.get_token("other/repository")
        assert requests == []
    finally:
        provider.close()
        client.close()


def test_provider_sanitizes_github_permission_errors():
    private_pem, public_key = _key_pair()
    provider, client, _ = _provider(
        lambda request: httpx.Response(403, json={"message": "private installation details"}),
        private_pem,
        public_key,
    )
    try:
        with pytest.raises(GitHubAppAuthError, match="github_app_token_http_403") as error:
            provider.get_token("acme/platform")
        assert "private installation details" not in str(error.value)
    finally:
        provider.close()
        client.close()


def test_provider_rejects_invalid_private_key_before_network():
    requests = []
    client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(
            lambda request: (requests.append(request), httpx.Response(201, json={}))[1]
        ),
    )
    provider = GitHubAppTokenProvider(
        app_id="12345",
        installation_id="67890",
        private_key_pem="-----BEGIN PRIVATE KEY-----\ninvalid\n-----END PRIVATE KEY-----",
        allowed_repositories={"acme/platform"},
        client=client,
    )
    try:
        with pytest.raises(GitHubAppAuthError, match="github_app_private_key_invalid"):
            provider.get_token("acme/platform")
        assert requests == []
    finally:
        provider.close()
        client.close()


def test_provider_rejects_client_with_untrusted_origin():
    private_pem, _ = _key_pair()
    client = httpx.Client(base_url="https://attacker.example")
    try:
        with pytest.raises(ValueError, match="official api.github.com"):
            GitHubAppTokenProvider(
                app_id="12345",
                installation_id="67890",
                private_key_pem=private_pem,
                allowed_repositories={"acme/platform"},
                client=client,
            )
    finally:
        client.close()
