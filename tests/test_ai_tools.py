"""Tests for the static, read-only, tenant-aware AI tool gateway."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.ai.memory.service import create_memory
from app.ai.runtime.tasks import create_task
from app.ai.tools.gateway import (
    ToolInputError,
    ToolNotFound,
    execute_tool,
    list_available_tools,
)
from app.core.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import AuditEvent, Organization, User


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
    owner = User(email="tool-owner@example.com", password_hash="test-hash")
    member = User(email="tool-member@example.com", password_hash="test-hash")
    organization = Organization(name="Tool Organization")
    db.add_all([owner, member, organization])
    db.commit()
    db.refresh(owner)
    db.refresh(member)
    db.refresh(organization)
    yield db, owner, member, organization
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_registry_exposes_only_static_read_only_tools():
    tools = list_available_tools()
    names = {item["name"] for item in tools}

    assert names == {
        "platform.health",
        "organization.profile",
        "ai.memory.search",
        "ai.skills.list",
        "ai.skills.get",
        "ai.tasks.get",
    }
    assert all(item["risk"] == "read_only" for item in tools)
    assert all("input_schema" in item for item in tools)


def test_health_and_organization_profile_are_audited(database):
    db, owner, _, organization = database
    health = execute_tool(
        db, actor_user_id=owner.id, organization_id=organization.id,
        role="owner", tool_name="platform.health", arguments={},
    )
    profile = execute_tool(
        db, actor_user_id=owner.id, organization_id=organization.id,
        role="owner", tool_name="organization.profile", arguments={},
    )

    assert health == {"status": "ok", "database": "reachable"}
    assert profile["organization_id"] == organization.id
    events = db.scalars(select(AuditEvent).where(
        AuditEvent.organization_id == organization.id,
        AuditEvent.action == "ai.tool.executed",
    )).all()
    assert len(events) == 2
    assert all(event.details.get("status") == "success" for event in events)


def test_memory_tool_uses_user_and_organization_visibility(database):
    db, owner, member, organization = database
    create_memory(
        db, actor_user_id=owner.id, scope="user", organization_id=None,
        memory_type="preference", content="Private memory retrieval sentinel",
    )
    create_memory(
        db, actor_user_id=owner.id, scope="organization", organization_id=organization.id,
        memory_type="project", content="Shared memory retrieval sentinel",
    )

    owner_result = execute_tool(
        db, actor_user_id=owner.id, organization_id=organization.id,
        role="owner", tool_name="ai.memory.search",
        arguments={"query": "retrieval sentinel", "limit": 10},
    )
    member_result = execute_tool(
        db, actor_user_id=member.id, organization_id=organization.id,
        role="member", tool_name="ai.memory.search",
        arguments={"query": "retrieval sentinel", "limit": 10},
    )

    owner_text = " ".join(item["content"] for item in owner_result["matches"])
    member_text = " ".join(item["content"] for item in member_result["matches"])
    assert "Private memory" in owner_text
    assert "Shared memory" in owner_text
    assert "Private memory" not in member_text
    assert "Shared memory" in member_text


def test_task_lookup_tool_enforces_creator_scope(database):
    db, owner, member, organization = database
    task, _ = create_task(
        db,
        organization_id=organization.id,
        actor_user_id=owner.id,
        task_type="agent.inspect",
        title="Private task",
        input_payload={"scope": "test"},
        idempotency_key="tool-task-key-01",
    )

    with pytest.raises(ToolNotFound):
        execute_tool(
            db, actor_user_id=member.id, organization_id=organization.id,
            role="member", tool_name="ai.tasks.get",
            arguments={"task_id": task.id},
        )
    visible = execute_tool(
        db, actor_user_id=owner.id, organization_id=organization.id,
        role="member", tool_name="ai.tasks.get",
        arguments={"task_id": task.id},
    )
    assert visible["task_id"] == task.id
    assert "input_payload" not in visible


def test_unknown_tools_and_extra_arguments_fail_closed(database):
    db, owner, _, organization = database
    with pytest.raises(ToolNotFound):
        execute_tool(
            db, actor_user_id=owner.id, organization_id=organization.id,
            role="owner", tool_name="shell.execute", arguments={"command": "id"},
        )
    with pytest.raises(ToolInputError):
        execute_tool(
            db, actor_user_id=owner.id, organization_id=organization.id,
            role="owner", tool_name="platform.health",
            arguments={"url": "https://untrusted.invalid"},
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
        json={"email": "tool-api-owner@example.com", "password": password},
    ).status_code == 201
    login = client.post(
        "/api/v1/auth/token",
        json={"email": "tool-api-owner@example.com", "password": password},
    )
    token = login.json()["access_token"]
    org = client.post(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Tool API Organization"},
    )
    assert org.status_code == 201, org.text
    return token, org.json()["id"]


def test_tool_api_lists_and_executes_only_registered_tools(api_client):
    token, organization_id = _owner(api_client)
    base = f"/api/v1/organizations/{organization_id}/ai/tools"
    headers = {"Authorization": f"Bearer {token}"}
    listed = api_client.get(base, headers=headers)
    assert listed.status_code == 200
    assert listed.json()["execution_mode"] == "read_only"
    assert len(listed.json()["tools"]) == 6

    health = api_client.post(
        f"{base}/platform.health/execute", headers=headers, json={},
    )
    assert health.status_code == 200, health.text
    assert health.json()["result"]["status"] == "ok"

    unknown = api_client.post(
        f"{base}/shell.execute/execute", headers=headers,
        json={"command": "id"},
    )
    assert unknown.status_code == 404



def test_skill_get_tool_only_returns_approved_versions_to_members(database):
    from app.ai.skills.service import approve_skill_version, create_skill_version

    db, owner, member, organization = database
    skill = create_skill_version(
        db,
        organization_id=organization.id,
        actor_user_id=owner.id,
        skill_id="research-method",
        version="1.0.0",
        title="Research method",
        description="Approved research instructions",
        content="Prefer primary sources and preserve provenance.",
        source_type="user_authored",
        license_id="user-owned",
    )
    approve_skill_version(
        db,
        organization_id=organization.id,
        skill_id=skill.skill_id,
        version=skill.version,
        actor_user_id=owner.id,
    )
    result = execute_tool(
        db,
        actor_user_id=member.id,
        organization_id=organization.id,
        role="member",
        tool_name="ai.skills.get",
        arguments={"skill_id": skill.skill_id, "version": skill.version},
    )
    assert result["content"] == "Prefer primary sources and preserve provenance."
    assert result["status"] == "approved"
