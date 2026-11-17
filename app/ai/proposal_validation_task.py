"""Durable worker handler for trusted, isolated proposal validation."""
from __future__ import annotations

from dataclasses import asdict
import hmac
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from app.ai.change_proposals import ProposedFile, ProposalValidationError, validate_proposal
from app.ai.proposal_sandbox import run_proposal_validation_suite, sign_validation_suite_result
from app.ai.sandbox_runner import DockerSandboxRunner
from app.ai.runtime.tasks import PermanentTaskError, TaskExecutionContext
from app.audit import record_audit_event
from app.core.config import settings, _parse_github_repository_allowlist
from app.models import AITask, Membership


class ProposedFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=65_536)
    mode: Literal["100644"] = "100644"


class ProposalValidationInput(BaseModel):
    """Only proposal content is task-controlled; commands/tests/image are operator-controlled."""
    model_config = ConfigDict(extra="forbid", strict=True)

    source_task_id: str | None = Field(default=None, min_length=1, max_length=64)
    repository: str | None = Field(
        default=None,
        min_length=3,
        max_length=201,
        pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$",
    )
    base_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    files: list[ProposedFileInput] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_source_shape(self):
        if self.source_task_id:
            if self.repository is not None or self.base_sha is not None or self.files is not None:
                raise ValueError("source_task_id must be used without inline proposal fields")
        elif not self.repository or not self.base_sha or not self.files:
            raise ValueError("repository, base_sha and files are required for inline proposals")
        return self


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
            or any(not isinstance(arg, str) or not arg or len(arg) > 2048 or "\x00" in arg for arg in commands[name])
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
        expected_digest = None
        if payload.source_task_id:
            source = context.db.scalar(select(AITask).where(
                AITask.id == payload.source_task_id,
                AITask.organization_id == task.organization_id,
                AITask.created_by_user_id == task.created_by_user_id,
            ))
            if (
                source is None
                or source.task_type != "proposal.generate"
                or source.status != "succeeded"
                or not isinstance(source.result_payload, dict)
                or source.result_payload.get("status") != "proposal_ready"
            ):
                raise PermanentTaskError("source_proposal_not_available")
            source_result = source.result_payload
            repository = source_result.get("repository")
            base_sha = source_result.get("base_sha")
            expected_digest = source_result.get("artifact_sha256")
            raw_files = source_result.get("files")
            if not isinstance(raw_files, list):
                raise PermanentTaskError("source_proposal_invalid")
            parsed_files = [ProposedFileInput.model_validate(item) for item in raw_files]
        else:
            repository = payload.repository
            base_sha = payload.base_sha
            parsed_files = payload.files or []
        if (
            not isinstance(repository, str)
            or not isinstance(base_sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", base_sha)
        ):
            raise PermanentTaskError("proposal_repository_binding_invalid")
        allowlist = _parse_github_repository_allowlist(settings.github_repository_allowlist)
        if repository not in allowlist:
            raise PermanentTaskError("repository_not_allowlisted")
        proposal = validate_proposal([
            ProposedFile(path=item.path, content=item.content, mode=item.mode)
            for item in parsed_files
        ])
        if expected_digest is not None and not hmac.compare_digest(proposal.sha256, str(expected_digest)):
            raise PermanentTaskError("source_proposal_digest_mismatch")
    except PermanentTaskError:
        raise
    except RuntimeError:
        raise PermanentTaskError("repository_allowlist_invalid") from None
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

    if suite.artifact_sha256 != proposal.sha256:
        raise PermanentTaskError("sandbox_artifact_digest_mismatch")

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
        "repository": repository,
        "base_sha": base_sha,
        "paths": list(proposal.paths),
        "checks": safe_checks,
        "attestation": None,
    }
    if suite.passed:
        signed = sign_validation_suite_result(
            suite,
            attestation_key=signing_key,
            repository=repository,
            base_sha=base_sha,
        )
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
            "repository": repository,
            "base_sha": base_sha,
        },
    )
    context.db.commit()
    return result
