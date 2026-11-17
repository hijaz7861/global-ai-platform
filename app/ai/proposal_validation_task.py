"""Durable worker handler for trusted, isolated proposal validation."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.ai.change_proposals import ProposedFile, ProposalValidationError, validate_proposal
from app.ai.evidence_attestation import sign_attestation
from app.ai.proposal_sandbox import run_proposal_validation_suite, sign_validation_suite_result
from app.ai.sandbox_runner import DockerSandboxRunner
from app.ai.runtime.tasks import PermanentTaskError, TaskExecutionContext
from app.audit import record_audit_event
from app.core.config import settings
from app.models import Membership


class ProposalValidationInput(BaseModel):
    """Only proposal content is task-controlled; commands/tests/image are operator-controlled."""
    model_config = ConfigDict(extra="forbid")

    files: list[dict[str, Any]] = Field(min_length=1, max_length=20)


def _runtime_configuration() -> tuple[DockerSandboxRunner, Path, dict[str, list[str]], bytes]:
    if not settings.ai_sandbox_enabled:
        raise PermanentTaskError("sandbox_validation_disabled")
    if settings.environment.strip().lower() not in {"staging", "production"}:
        raise PermanentTaskError("sandbox_requires_trusted_runtime")
    if not settings.ai_sandbox_image or not settings.ai_sandbox_trusted_test_directory:
        raise PermanentTaskError("sandbox_runtime_incomplete")
    try:
        trusted_directory = Path(settings.ai_sandbox_trusted_test_directory).resolve(strict=True)
        if not trusted_directory.is_dir():
            raise ValueError
        commands = json.loads(settings.ai_sandbox_validation_commands)
        if not isinstance(commands, dict) or set(commands) != {"unit", "security", "regression"}:
            raise ValueError
        if any(
            not isinstance(commands[name], list)
            or not commands[name]
            or len(commands[name]) > 64
            or any(not isinstance(arg, str) or not arg or len(arg) > 2048 or "\\x00" in arg for arg in commands[name])
            for name in ("unit", "security", "regression")
        ):
            raise ValueError
        key = settings.ai_sandbox_attestation_hmac_key
        if key is None or len(key.get_secret_value().encode("utf-8")) < 32:
            raise ValueError
        runner = DockerSandboxRunner(
            image=settings.ai_sandbox_image,
            timeout_seconds=settings.ai_sandbox_timeout_seconds,
            memory_limit=settings.ai_sandbox_memory_limit,
            cpu_limit=settings.ai_sandbox_cpu_limit,
            pids_limit=settings.ai_sandbox_pids_limit,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise PermanentTaskError("sandbox_runtime_invalid") from None
    return runner, trusted_directory, commands, key.get_secret_value().encode("utf-8")


def run_proposal_validation_task(context: TaskExecutionContext) -> dict:
    """Run trusted checks; sign only passing results. Never writes to a repository."""
    task = context.task
    membership = context.db.scalar(select(Membership).where(
        Membership.user_id == task.created_by_user_id,
        Membership.organization_id == task.organization_id,
    ))
    if membership is None or membership.role not in {"owner", "admin"}:
        raise PermanentTaskError("membership_revoked")

    try:
        payload = ProposalValidationInput.model_validate(task.input_payload)
        files = [
            ProposedFile(
                path=item["path"],
                content=item["content"],
                mode=item.get("mode", "100644"),
            )
            for item in payload.files
            if set(item).issubset({"path", "content", "mode"})
            and "path" in item and "content" in item
        ]
        if len(files) != len(payload.files):
            raise ValueError
        proposal = validate_proposal(files)
    except (ValidationError, KeyError, TypeError, ValueError, ProposalValidationError):
        raise PermanentTaskError("invalid_proposal_input") from None

    runner, trusted_directory, commands, signing_key = _runtime_configuration()
    context.heartbeat()
    try:
        suite = run_proposal_validation_suite(
            proposal,
            runner=runner,
            trusted_test_directory=trusted_directory,
            test_command=commands["unit"],
            security_command=commands["security"],
            regression_command=commands["regression"],
            on_check_start=context.heartbeat,
        )
    except (RuntimeError, OSError, ValueError):
        raise PermanentTaskError("sandbox_execution_failed") from None

    checks = {
        "unit": suite.tests,
        "security": suite.security_scan,
        "regression": suite.regression,
    }
    safe_checks = {
        name: {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "output_limit_exceeded": result.output_limit_exceeded,
            "duration_seconds": result.duration_seconds,
        }
        for name, result in checks.items()
    }
    result: dict[str, Any] = {
        "status": "passed" if suite.passed else "failed",
        "artifact_sha256": proposal.sha256,
        "paths": list(proposal.paths),
        "checks": safe_checks,
        "attestation": None,
    }
    if suite.passed:
        signed = sign_validation_suite_result(suite, attestation_key=signing_key)
        result["attestation"] = {
            "payload": asdict(signed.payload),
            "signature_hex": signed.signature_hex,
        }

    record_audit_event(
        context.db,
        actor_user_id=task.created_by_user_id,
        organization_id=task.organization_id,
        action="ai.proposal.validation.completed",
        entity_type="ai_proposal",
        entity_id=proposal.sha256,
        details={
            "status": result["status"],
            "checks": {
                name: {
                    "exit_code": check["exit_code"],
                    "timed_out": check["timed_out"],
                    "output_limit_exceeded": check["output_limit_exceeded"],
                }
                for name, check in safe_checks.items()
            },
            "task_id": task.id,
        },
    )
    context.db.commit()
    return result
