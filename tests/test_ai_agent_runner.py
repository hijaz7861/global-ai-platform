"""Tests for the constrained single-agent loop without live provider calls."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.ai.agents import runner
from app.ai.agents.runner import AgentRunError, run_agent_task
from app.ai.runtime.tasks import create_task
from app.db import Base
from app.models import Membership, Organization, User


@pytest.fixture
def agent_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    db = factory()
    user = User(email="agent-owner@example.com", password_hash="test-hash")
    organization = Organization(name="Agent Organization")
    db.add_all([user, organization])
    db.commit()
    db.refresh(user)
    db.refresh(organization)
    db.add(Membership(user_id=user.id, organization_id=organization.id, role="owner"))
    db.commit()
    task, _ = create_task(
        db,
        organization_id=organization.id,
        actor_user_id=user.id,
        task_type="agent.run",
        title="Research organization profile",
        input_payload={
            "goal": "Read the organization profile and summarize it",
            "model_route": "primary",
            "task_type": "analyze",
            "data_class": "internal",
            "max_steps": 2,
            "max_output_tokens": 64,
        },
        idempotency_key="agent-run-key-001",
    )
    context = SimpleNamespace(
        db=db,
        task=task,
        worker_id="agent-test-worker",
        lease_seconds=300,
        heartbeat=lambda: None,
    )
    yield context, user, organization
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


class FakeRouter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.selections = []
        self.calls = []

    def select(self, **kwargs):
        self.selections.append(kwargs)
        return object()

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _model_response(content: dict) -> dict:
    return {
        "provider": "stub",
        "model": "stub-model",
        "content": json.dumps(content),
        "input_tokens": 10,
        "output_tokens": 5,
    }


def _patch_runner(monkeypatch, router_instance):
    monkeypatch.setattr(runner.settings, "ai_live_calls_enabled", True)
    monkeypatch.setattr(runner, "create_model_router", lambda config: router_instance)
    monkeypatch.setattr(runner, "_reserve_model_call", lambda *args, **kwargs: "request-test-id")
    monkeypatch.setattr(runner, "_finish_model_call", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_fail_model_call", lambda *args, **kwargs: None)


def test_agent_uses_static_tool_then_returns_final_answer(agent_context, monkeypatch):
    context, _, organization = agent_context
    router_instance = FakeRouter([
        _model_response({"action": "tool", "tool": "organization.profile", "arguments": {}}),
        _model_response({"action": "final", "answer": "The organization profile was checked."}),
    ])
    _patch_runner(monkeypatch, router_instance)

    result = asyncio.run(run_agent_task(context))

    assert result["status"] == "completed"
    assert result["answer"] == "The organization profile was checked."
    assert result["steps"][0]["tool"] == "organization.profile"
    assert result["usage"]["model_calls"] == 2
    assert router_instance.selections[0]["data_class"] == "internal"
    assert organization.id in router_instance.calls[1]["prompt"]


def test_agent_rejects_model_requested_unregistered_tool(agent_context, monkeypatch):
    context, _, _ = agent_context
    router_instance = FakeRouter([
        _model_response({
            "action": "tool",
            "tool": "shell.execute",
            "arguments": {"command": "id"},
        }),
    ])
    _patch_runner(monkeypatch, router_instance)

    with pytest.raises(AgentRunError) as exc:
        asyncio.run(run_agent_task(context))
    assert exc.value.error_code == "unregistered_tool_call"


def test_agent_stops_at_configured_step_limit_with_explicit_status(agent_context, monkeypatch):
    context, _, _ = agent_context
    context.task.input_payload = {
        "goal": "Inspect the organization profile",
        "model_route": "primary",
        "task_type": "analyze",
        "data_class": "internal",
        "max_steps": 1,
        "max_output_tokens": 64,
    }
    router_instance = FakeRouter([
        _model_response({"action": "tool", "tool": "organization.profile", "arguments": {}}),
    ])
    _patch_runner(monkeypatch, router_instance)

    result = asyncio.run(run_agent_task(context))
    assert result["status"] == "step_limit"
    assert result["usage"]["model_calls"] == 1
    assert len(result["steps"]) == 1


def test_agent_stops_when_organization_quota_is_exhausted(agent_context, monkeypatch):
    context, _, _ = agent_context
    router_instance = FakeRouter([])
    _patch_runner(monkeypatch, router_instance)

    def exhausted(*args, **kwargs):
        raise AgentRunError("ai_quota_exceeded")

    monkeypatch.setattr(runner, "_reserve_model_call", exhausted)
    result = asyncio.run(run_agent_task(context))
    assert result["status"] == "quota_exhausted"
    assert result["usage"]["model_calls"] == 0
    assert router_instance.calls == []


def test_agent_rechecks_membership_before_running(agent_context, monkeypatch):
    context, user, organization = agent_context
    membership = context.db.query(Membership).filter_by(
        user_id=user.id, organization_id=organization.id
    ).one()
    membership.role = "member"
    context.db.commit()
    router_instance = FakeRouter([])
    _patch_runner(monkeypatch, router_instance)

    with pytest.raises(AgentRunError) as exc:
        asyncio.run(run_agent_task(context))
    assert exc.value.error_code == "membership_revoked"
    assert router_instance.calls == []
