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


def test_readiness_endpoint_checks_database():
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_endpoint_fails_closed_without_database(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    from app.db import get_db

    class UnavailableDatabase:
        def execute(self, statement):
            raise SQLAlchemyError("private connection detail")

    def broken_db():
        yield UnavailableDatabase()

    monkeypatch.setitem(app.dependency_overrides, get_db, broken_db)
    response = client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is not ready"}
    assert "private connection detail" not in response.text
