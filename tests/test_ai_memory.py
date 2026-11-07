"""Tests for provenance, BM25 recall, scope isolation, and memory lifecycle."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.ai.memory.service import (
    MemoryNotFound,
    archive_memory,
    create_memory,
    get_memory,
    search_memories,
)
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Organization, User


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    db = factory()
    user = User(email="memory-owner@example.com", password_hash="test-hash")
    other = User(email="memory-other@example.com", password_hash="test-hash")
    organization = Organization(name="Memory Organization")
    db.add_all([user, other, organization])
    db.commit()
    db.refresh(user)
    db.refresh(other)
    db.refresh(organization)
    yield db, user, other, organization
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_user_and_organization_memory_have_distinct_visibility(database):
    db, user, other, organization = database
    private = create_memory(
        db, actor_user_id=user.id, scope="user", organization_id=None,
        memory_type="preference", content="Prefers concise Urdu technical explanations",
    )
    shared = create_memory(
        db, actor_user_id=user.id, scope="organization", organization_id=organization.id,
        memory_type="project", content="The platform uses PostgreSQL for production task coordination",
    )

    owner_results = search_memories(
        db, actor_user_id=user.id, organization_id=organization.id, role="owner"
    )
    other_results = search_memories(
        db, actor_user_id=other.id, organization_id=organization.id, role="member"
    )

    assert {item.record.id for item in owner_results} == {private.id, shared.id}
    assert [item.record.id for item in other_results] == [shared.id]


def test_bm25_retrieval_ranks_matching_records_and_explains_terms(database):
    db, user, _, organization = database
    weak = create_memory(
        db, actor_user_id=user.id, scope="organization", organization_id=organization.id,
        memory_type="reference", content="A general platform note about commerce and accounting",
    )
    strong = create_memory(
        db, actor_user_id=user.id, scope="organization", organization_id=organization.id,
        memory_type="decision", content="PostgreSQL worker leases coordinate durable AI task execution",
    )

    results = search_memories(
        db, actor_user_id=user.id, organization_id=organization.id, role="owner",
        query="PostgreSQL worker leases", limit=10,
    )

    assert results[0].record.id == strong.id
    assert "postgresql" in results[0].matched_terms
    assert results[0].score > 0
    assert all(item.record.id != weak.id for item in results)


def test_restricted_memory_is_hidden_from_non_admin_members(database):
    db, user, other, organization = database
    record = create_memory(
        db, actor_user_id=user.id, scope="organization", organization_id=organization.id,
        memory_type="reference", content="Restricted internal operational note",
        sensitivity="restricted",
    )

    member_results = search_memories(
        db, actor_user_id=other.id, organization_id=organization.id, role="member"
    )
    admin_results = search_memories(
        db, actor_user_id=other.id, organization_id=organization.id, role="admin"
    )
    owner_results = search_memories(
        db, actor_user_id=user.id, organization_id=organization.id, role="owner"
    )

    assert all(item.record.id != record.id for item in member_results)
    assert all(item.record.id != record.id for item in admin_results)
    assert any(item.record.id == record.id for item in owner_results)


def test_superseding_memory_preserves_history_and_updates_active_value(database):
    db, user, _, organization = database
    original = create_memory(
        db, actor_user_id=user.id, scope="user", organization_id=None,
        memory_type="project", content="The project uses SQLite in production",
    )
    replacement = create_memory(
        db, actor_user_id=user.id, scope="user", organization_id=None,
        memory_type="project", content="Correction: PostgreSQL is the production database",
        supersedes_id=original.id,
    )
    db.refresh(original)

    assert original.status == "superseded"
    assert original.verification_status == "superseded"
    assert replacement.supersedes_id == original.id
    active = search_memories(
        db, actor_user_id=user.id, organization_id=organization.id, role="owner",
        query="production database",
    )
    assert [item.record.id for item in active] == [replacement.id]


def test_memory_expiry_requires_timezone_and_expired_records_are_excluded(database):
    db, user, _, organization = database
    with pytest.raises(ValueError, match="include a timezone"):
        create_memory(
            db, actor_user_id=user.id, scope="user", organization_id=None,
            memory_type="reference", content="Naive expiry is not accepted",
            expires_at=datetime.now(),
        )
    record = create_memory(
        db, actor_user_id=user.id, scope="organization", organization_id=organization.id,
        memory_type="reference", content="Short-lived operational note",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    results = search_memories(
        db, actor_user_id=user.id, organization_id=organization.id, role="owner",
    )
    assert all(item.record.id != record.id for item in results)


def test_archived_memory_is_not_retrievable(database):
    db, user, _, organization = database
    record = create_memory(
        db, actor_user_id=user.id, scope="user", organization_id=None,
        memory_type="feedback", content="An old preference to archive",
    )
    archive_memory(
        db, memory_id=record.id, actor_user_id=user.id,
        organization_id=organization.id, role="member",
    )
    with pytest.raises(MemoryNotFound):
        get_memory(
            db, memory_id=record.id, actor_user_id=user.id,
            organization_id=organization.id, role="member",
        )


@pytest.fixture
def api_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "secret_key", "test-only-signing-key-not-for-production")

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _owner(client: TestClient) -> tuple[str, str]:
    password = "correct-horse-battery-12"
    assert client.post(
        "/api/v1/auth/register",
        json={"email": "memory-api-owner@example.com", "password": password},
    ).status_code == 201
    login = client.post(
        "/api/v1/auth/token",
        json={"email": "memory-api-owner@example.com", "password": password},
    )
    token = login.json()["access_token"]
    org = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Memory API Organization"},
    )
    assert org.status_code == 201, org.text
    return token, org.json()["id"]


def test_memory_api_creates_searches_and_archives_private_memory(api_client):
    token, organization_id = _owner(api_client)
    url = f"/api/v1/organizations/{organization_id}/ai/memory"
    headers = {"Authorization": f"Bearer {token}"}
    created = api_client.post(url, headers=headers, json={
        "scope": "user",
        "memory_type": "preference",
        "content": "Prefers Urdu explanations with exact technical details",
        "source_type": "user_input",
    })
    assert created.status_code == 201, created.text
    assert created.json()["verification_status"] == "unverified"
    assert len(created.json()["content_sha256"]) == 64

    searched = api_client.get(url, headers=headers, params={"q": "Urdu technical"})
    assert searched.status_code == 200, searched.text
    assert len(searched.json()) == 1
    assert "urdu" in searched.json()[0]["matched_terms"]

    archived = api_client.delete(f"{url}/{created.json()['id']}", headers=headers)
    assert archived.status_code == 204
    after = api_client.get(url, headers=headers)
    assert after.status_code == 200
    assert after.json() == []
