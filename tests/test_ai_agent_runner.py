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
from app.ai.runtime.tasks import claim_next_task, create_task, start_task_step
from app.ai.agents.checkpoints import save_agent_checkpoint
from app.ai.providers import ProviderError
from app.ai.memory.service import create_memory
from app.ai.skills.service import approve_skill_version, create_skill_version
from app.ai.runtime.worker import run_worker_once
from app.db import Base
from app.models import AuditEvent, Membership, Organization, User


@pytest.fixture
def agent_context(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(runner.settings, "secret_key", "agent-checkpoint-test-secret-key-0001")
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
        max_attempts=3,
    )
    worker_id = "agent-test-worker"
    task = claim_next_task(db, worker_id=worker_id, lease_seconds=300)
    assert task is not None
    start_task_step(db, task=task, worker_id=worker_id, step_name="agent.run")
    context = SimpleNamespace(
        db=db,
        task=task,
        worker_id=worker_id,
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
        return SimpleNamespace(route=kwargs["route"], provider_name=kwargs["route"], model_name=str(kwargs["route"]) + "-model")

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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



def test_durable_worker_executes_agent_run_task_end_to_end(agent_context, monkeypatch):
    context, _, _ = agent_context
    router_instance = FakeRouter([
        _model_response({"action": "final", "answer": "The durable agent completed its task."}),
    ])
    _patch_runner(monkeypatch, router_instance)

    assert run_worker_once(
        context.db,
        worker_id="durable-agent-worker",
        lease_seconds=300,
    ) is True
    context.db.refresh(context.task)

    assert context.task.status == "succeeded"
    assert context.task.result_payload["status"] == "completed"
    assert context.task.result_payload["answer"] == "The durable agent completed its task."
    assert context.task.max_attempts == 1



def test_agent_uses_only_visible_memory_and_owner_approved_matching_skills(agent_context, monkeypatch):
    context, user, organization = agent_context
    context.task.input_payload["goal"] = "PostgreSQL worker leases durable repository review"
    create_memory(
        context.db,
        actor_user_id=user.id,
        scope="user",
        organization_id=None,
        memory_type="decision",
        content="The repository uses PostgreSQL worker leases for durable execution.",
        source_type="manual",
        confidence_percent=80,
    )
    approved = create_skill_version(
        context.db,
        organization_id=organization.id,
        actor_user_id=user.id,
        skill_id="repository-review",
        version="1.0.0",
        title="Repository review",
        description="Review repository changes and tests",
        content="Inspect the changed files and verify regression tests.",
        source_type="user_authored",
        license_id="user-owned",
        data_classes=["internal"],
        risk_level="low",
    )
    approve_skill_version(
        context.db,
        organization_id=organization.id,
        skill_id=approved.skill_id,
        version=approved.version,
        actor_user_id=user.id,
    )
    create_skill_version(
        context.db,
        organization_id=organization.id,
        actor_user_id=user.id,
        skill_id="unapproved-review",
        version="1.0.0",
        title="Unapproved review",
        description="Must not enter the agent prompt",
        content="This draft must never be used by the agent.",
        source_type="user_authored",
        license_id="user-owned",
        data_classes=["internal"],
        risk_level="low",
    )
    public_only = create_skill_version(
        context.db,
        organization_id=organization.id,
        actor_user_id=user.id,
        skill_id="public-only",
        version="1.0.0",
        title="Public-only guidance",
        description="Not applicable to internal tasks",
        content="This public-only skill must not enter an internal prompt.",
        source_type="user_authored",
        license_id="user-owned",
        data_classes=["public"],
        risk_level="low",
    )
    approve_skill_version(
        context.db,
        organization_id=organization.id,
        skill_id=public_only.skill_id,
        version=public_only.version,
        actor_user_id=user.id,
    )

    router_instance = FakeRouter([
        _model_response({"action": "final", "answer": "Context was loaded safely."}),
    ])
    _patch_runner(monkeypatch, router_instance)
    result = asyncio.run(run_agent_task(context))

    prompt = router_instance.calls[0]["prompt"]
    assert result["status"] == "completed"
    assert "PostgreSQL worker leases" in prompt
    assert "Inspect the changed files" in prompt
    assert "unapproved-review" not in prompt
    assert "public-only" not in prompt
    assert '"verification_status":"unverified"' in prompt
    assert "untrusted context" in prompt



def test_agent_automatically_fails_over_only_after_retryable_primary_error(agent_context, monkeypatch):
    context, _, _ = agent_context
    monkeypatch.setattr(runner.settings, "ai_auto_failover_enabled", True)
    router_instance = FakeRouter([
        ProviderError("temporary upstream failure", retryable=True),
        _model_response({"action": "final", "answer": "Fallback provider completed the task."}),
    ])
    _patch_runner(monkeypatch, router_instance)
    reservations = []
    failures = []
    monkeypatch.setattr(
        runner, "_reserve_model_call",
        lambda *args, **kwargs: reservations.append(len(reservations) + 1) or f"request-{len(reservations)}",
    )
    monkeypatch.setattr(
        runner, "_fail_model_call",
        lambda db, *, request_id, **kwargs: failures.append(request_id),
    )

    result = asyncio.run(run_agent_task(context))

    assert result["status"] == "completed"
    assert result["answer"] == "Fallback provider completed the task."
    assert [call["route"] for call in router_instance.calls] == ["primary", "fallback"]
    assert len(reservations) == 2
    assert failures == ["request-1"]
    assert result["usage"]["model_calls"] == 2


def test_agent_does_not_fail_over_on_non_retryable_provider_error(agent_context, monkeypatch):
    context, _, _ = agent_context
    monkeypatch.setattr(runner.settings, "ai_auto_failover_enabled", True)
    router_instance = FakeRouter([ProviderError("invalid credentials", retryable=False)])
    _patch_runner(monkeypatch, router_instance)

    with pytest.raises(AgentRunError) as exc:
        asyncio.run(run_agent_task(context))

    assert exc.value.error_code == "model_provider_failed"
    assert [call["route"] for call in router_instance.calls] == ["primary"]



def test_daily_token_budget_is_reserved_under_organization_lock(agent_context, monkeypatch):
    context, user, organization = agent_context
    monkeypatch.setattr(runner.settings, "database_url", "postgresql+psycopg://test")
    monkeypatch.setattr(runner.settings, "ai_daily_request_limit", 10)
    monkeypatch.setattr(runner.settings, "ai_daily_token_limit", 100)

    with pytest.raises(AgentRunError) as exc:
        runner._reserve_model_call(
            context.db,
            organization_id=organization.id,
            actor_user_id=user.id,
            estimated_tokens=101,
        )
    assert exc.value.error_code == "ai_token_quota_exceeded"
    assert context.db.query(AuditEvent).filter_by(
        organization_id=organization.id,
        action="ai.request.started",
    ).count() == 0


def test_daily_token_budget_records_conservative_reservation(agent_context, monkeypatch):
    context, user, organization = agent_context
    monkeypatch.setattr(runner.settings, "database_url", "postgresql+psycopg://test")
    monkeypatch.setattr(runner.settings, "ai_daily_request_limit", 10)
    monkeypatch.setattr(runner.settings, "ai_daily_token_limit", 100)

    request_id = runner._reserve_model_call(
        context.db,
        organization_id=organization.id,
        actor_user_id=user.id,
        estimated_tokens=80,
    )
    event = context.db.query(AuditEvent).filter_by(
        organization_id=organization.id,
        action="ai.request.started",
        entity_id=request_id,
    ).one()
    assert event.details["estimated_tokens"] == 80
    assert event.details["token_estimation"] == "utf8_bytes_plus_max_output"



def test_agent_persists_result_only_when_user_opts_in(agent_context, monkeypatch):
    context, user, organization = agent_context
    context.task.input_payload["remember_result"] = True
    router_instance = FakeRouter([
        _model_response({
            "action": "final",
            "answer": "PostgreSQL leases recover queued agent tasks after worker interruption.",
        }),
    ])
    _patch_runner(monkeypatch, router_instance)

    result = asyncio.run(run_agent_task(context))

    assert result["memory_saved"] is True
    from app.ai.memory.service import search_memories
    matches = search_memories(
        context.db,
        actor_user_id=user.id,
        organization_id=organization.id,
        role="owner",
        query="PostgreSQL leases recover queued tasks",
        limit=5,
    )
    saved = next(item.record for item in matches if item.record.source_ref == context.task.id)
    assert saved.scope == "user"
    assert saved.organization_id is None
    assert saved.source_type == "task"
    assert saved.memory_type == "session_summary"
    assert saved.verification_status == "unverified"
    assert saved.sensitivity == "sensitive"
    assert saved.expires_at is not None


def test_agent_does_not_persist_result_by_default(agent_context, monkeypatch):
    context, user, organization = agent_context
    router_instance = FakeRouter([
        _model_response({"action": "final", "answer": "This answer remains transient."}),
    ])
    _patch_runner(monkeypatch, router_instance)

    result = asyncio.run(run_agent_task(context))

    assert result["memory_saved"] is False
    from app.ai.memory.service import search_memories
    assert search_memories(
        context.db,
        actor_user_id=user.id,
        organization_id=organization.id,
        role="owner",
        query="transient answer",
        limit=5,
    ) == []


def test_agent_skips_open_primary_and_uses_healthy_fallback(agent_context, monkeypatch):
    context, _, _ = agent_context
    monkeypatch.setattr(runner.settings, "ai_provider_circuit_breaker_enabled", True)
    monkeypatch.setattr(runner.settings, "ai_auto_failover_enabled", True)
    monkeypatch.setattr(runner.settings, "database_url", "postgresql+psycopg://test")
    router_instance = FakeRouter([
        _model_response({"action": "final", "answer": "Fallback was selected after the primary circuit opened."}),
    ])
    _patch_runner(monkeypatch, router_instance)
    claims = []
    successes = []

    def claim(_db, *, provider_name, model_name, **kwargs):
        claims.append(provider_name)
        return provider_name == "fallback"

    monkeypatch.setattr(runner, "claim_provider_attempt", claim)
    monkeypatch.setattr(
        runner, "record_provider_success",
        lambda _db, **kwargs: successes.append(kwargs["provider_name"]),
    )

    result = asyncio.run(run_agent_task(context))

    assert result["status"] == "completed"
    assert [call["route"] for call in router_instance.calls] == ["fallback"]
    assert claims == ["primary", "fallback"]
    assert successes == ["fallback"]
    assert result["usage"]["model_calls"] == 1


def test_agent_fails_closed_when_primary_circuit_is_open_and_failover_is_disabled(agent_context, monkeypatch):
    context, _, _ = agent_context
    monkeypatch.setattr(runner.settings, "ai_provider_circuit_breaker_enabled", True)
    monkeypatch.setattr(runner.settings, "ai_auto_failover_enabled", False)
    monkeypatch.setattr(runner.settings, "database_url", "postgresql+psycopg://test")
    router_instance = FakeRouter([])
    _patch_runner(monkeypatch, router_instance)
    monkeypatch.setattr(runner, "claim_provider_attempt", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        runner, "_reserve_model_call",
        lambda *args, **kwargs: pytest.fail("quota must not be reserved for an open circuit"),
    )

    with pytest.raises(AgentRunError) as exc:
        asyncio.run(run_agent_task(context))

    assert exc.value.error_code == "model_provider_circuit_open"
    assert router_instance.calls == []


def test_agent_records_retryable_primary_failure_and_fallback_success(agent_context, monkeypatch):
    context, _, _ = agent_context
    monkeypatch.setattr(runner.settings, "ai_provider_circuit_breaker_enabled", True)
    monkeypatch.setattr(runner.settings, "ai_auto_failover_enabled", True)
    monkeypatch.setattr(runner.settings, "database_url", "postgresql+psycopg://test")
    router_instance = FakeRouter([
        ProviderError("temporary upstream failure", retryable=True),
        _model_response({"action": "final", "answer": "Fallback recovered."}),
    ])
    _patch_runner(monkeypatch, router_instance)
    failures = []
    successes = []
    monkeypatch.setattr(runner, "claim_provider_attempt", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        runner, "record_provider_failure",
        lambda _db, **kwargs: failures.append((kwargs["provider_name"], kwargs["retryable"])),
    )
    monkeypatch.setattr(
        runner, "record_provider_success",
        lambda _db, **kwargs: successes.append(kwargs["provider_name"]),
    )

    result = asyncio.run(run_agent_task(context))

    assert result["status"] == "completed"
    assert [call["route"] for call in router_instance.calls] == ["primary", "fallback"]
    assert failures == [("primary", True)]
    assert successes == ["fallback"]
    assert result["usage"]["model_calls"] == 2


def test_agent_does_not_call_an_open_fallback_after_primary_failure(agent_context, monkeypatch):
    context, _, _ = agent_context
    monkeypatch.setattr(runner.settings, "ai_provider_circuit_breaker_enabled", True)
    monkeypatch.setattr(runner.settings, "ai_auto_failover_enabled", True)
    monkeypatch.setattr(runner.settings, "database_url", "postgresql+psycopg://test")
    router_instance = FakeRouter([ProviderError("temporary upstream failure", retryable=True)])
    _patch_runner(monkeypatch, router_instance)
    claims = []
    failures = []

    def claim(_db, *, provider_name, model_name, **kwargs):
        claims.append(provider_name)
        return provider_name == "primary"

    monkeypatch.setattr(runner, "claim_provider_attempt", claim)
    monkeypatch.setattr(
        runner, "record_provider_failure",
        lambda _db, **kwargs: failures.append(kwargs["provider_name"]),
    )

    with pytest.raises(AgentRunError) as exc:
        asyncio.run(run_agent_task(context))

    assert exc.value.error_code == "model_fallback_circuit_open"
    assert claims == ["primary", "fallback"]
    assert failures == ["primary"]
    assert [call["route"] for call in router_instance.calls] == ["primary"]


def test_agent_circuit_breaker_requires_postgres(agent_context, monkeypatch):
    context, _, _ = agent_context
    monkeypatch.setattr(runner.settings, "ai_provider_circuit_breaker_enabled", True)
    monkeypatch.setattr(runner.settings, "database_url", "sqlite:///./unit-test.db")
    router_instance = FakeRouter([])
    _patch_runner(monkeypatch, router_instance)

    with pytest.raises(AgentRunError) as exc:
        asyncio.run(run_agent_task(context))

    assert exc.value.error_code == "postgres_required"
    assert router_instance.calls == []


def test_agent_counts_primary_attempt_when_fallback_quota_is_exhausted(agent_context, monkeypatch):
    context, _, _ = agent_context
    monkeypatch.setattr(runner.settings, "ai_provider_circuit_breaker_enabled", True)
    monkeypatch.setattr(runner.settings, "ai_auto_failover_enabled", True)
    monkeypatch.setattr(runner.settings, "database_url", "postgresql+psycopg://test")
    router_instance = FakeRouter([ProviderError("temporary upstream failure", retryable=True)])
    _patch_runner(monkeypatch, router_instance)
    monkeypatch.setattr(runner, "claim_provider_attempt", lambda *args, **kwargs: True)
    monkeypatch.setattr(runner, "record_provider_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "release_provider_attempt", lambda *args, **kwargs: None)
    reservations = []

    def reserve(*args, **kwargs):
        reservations.append(True)
        if len(reservations) == 1:
            return "primary-request"
        raise AgentRunError("ai_quota_exceeded")

    monkeypatch.setattr(runner, "_reserve_model_call", reserve)

    result = asyncio.run(run_agent_task(context))

    assert result["status"] == "quota_exhausted"
    assert result["usage"]["model_calls"] == 1
    assert [call["route"] for call in router_instance.calls] == ["primary"]



def test_agent_blocks_secret_in_goal_before_quota_reservation_or_provider_call(agent_context, monkeypatch):
    context, _, _ = agent_context
    context.task.input_payload["goal"] = "Review this repository token: ghp_" + "A" * 36
    router_instance = FakeRouter([])
    _patch_runner(monkeypatch, router_instance)

    monkeypatch.setattr(
        runner,
        "_reserve_model_call",
        lambda *args, **kwargs: pytest.fail("secret-bearing prompt must be blocked before quota reservation"),
    )

    with pytest.raises(AgentRunError) as exc:
        asyncio.run(run_agent_task(context))

    assert exc.value.error_code == "sensitive_data_detected"
    assert router_instance.calls == []


def test_agent_does_not_forward_secret_returned_by_read_only_tool(agent_context, monkeypatch):
    context, _, _ = agent_context
    context.task.input_payload["max_steps"] = 2
    router_instance = FakeRouter([
        _model_response({"action": "tool", "tool": "organization.profile", "arguments": {}}),
    ])
    _patch_runner(monkeypatch, router_instance)
    secret = "ghp_" + "B" * 36
    monkeypatch.setattr(runner, "execute_tool", lambda *args, **kwargs: {"note": secret})

    with pytest.raises(AgentRunError) as exc:
        asyncio.run(run_agent_task(context))

    assert exc.value.error_code == "sensitive_data_detected"
    assert len(router_instance.calls) == 1
    assert secret not in router_instance.calls[0]["prompt"]



def test_agent_resumes_saved_decision_without_repeating_provider_call(agent_context, monkeypatch):
    context, _, _ = agent_context
    memories, skills = runner._load_approved_context(
        context,
        goal="Read the organization profile and summarize it",
        data_class="internal",
    )
    state = runner._new_checkpoint_state()
    state["context_sha256"] = runner._approved_context_digest(memories, skills)
    state["decision"] = {
        "action": "final",
        "tool": None,
        "arguments": {},
        "answer": "Recovered from the durable checkpoint.",
    }
    state["total_model_calls"] = 1
    state["provider"] = "stub"
    state["model"] = "stub-model"
    save_agent_checkpoint(
        context.db,
        task=context.task,
        worker_id=context.worker_id,
        phase="decision_saved",
        turn=1,
        state=state,
        lease_seconds=context.lease_seconds,
    )
    router_instance = FakeRouter([])
    _patch_runner(monkeypatch, router_instance)

    result = asyncio.run(run_agent_task(context))

    assert result["status"] == "completed"
    assert result["answer"] == "Recovered from the durable checkpoint."
    assert result["usage"]["model_calls"] == 1
    assert router_instance.calls == []


def test_agent_rejects_checkpoint_when_approved_context_changes(agent_context, monkeypatch):
    context, _, _ = agent_context
    memories, skills = runner._load_approved_context(
        context,
        goal="Read the organization profile and summarize it",
        data_class="internal",
    )
    state = runner._new_checkpoint_state()
    state["context_sha256"] = "0" * 64
    state["decision"] = {
        "action": "final",
        "tool": None,
        "arguments": {},
        "answer": "Must not be returned.",
    }
    save_agent_checkpoint(
        context.db,
        task=context.task,
        worker_id=context.worker_id,
        phase="decision_saved",
        turn=1,
        state=state,
        lease_seconds=context.lease_seconds,
    )
    _patch_runner(monkeypatch, FakeRouter([]))

    with pytest.raises(AgentRunError) as exc:
        asyncio.run(run_agent_task(context))

    assert exc.value.error_code == "agent_checkpoint_context_changed"
