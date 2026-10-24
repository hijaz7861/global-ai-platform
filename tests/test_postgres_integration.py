"""PostgreSQL integration tests for refresh-token replay under concurrent requests.

These tests run only in the dedicated PostgreSQL CI job after Alembic migrations.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="Dedicated PostgreSQL integration database is required",
)


def test_concurrent_refresh_replay_revokes_session_family():
    email = f"postgres-refresh-{uuid4().hex}@example.com"
    password = "correct-horse-battery-12"
    with TestClient(app) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )
        assert registered.status_code == 201, registered.text
        login = client.post(
            "/api/v1/auth/token",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200, login.text
        refresh_token = login.json()["refresh_token"]

    def refresh_once():
        with TestClient(app) as client:
            return client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: refresh_once(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 401]
    successful = next(response for response in responses if response.status_code == 200)
    access = successful.json()["access_token"]
    with TestClient(app) as client:
        protected = client.get(
            "/api/v1/organizations",
            headers={"Authorization": f"Bearer {access}"},
        )
    # Concurrent reuse is treated as a suspected theft and invalidates the family.
    assert protected.status_code == 401
