"""Smoke tests for the initial HTTP contract."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_returns_service_identity() -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Global AI Platform"
    assert body["status"] == "available"


def test_versioned_health_endpoint() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_document_is_available() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Global AI Platform"
