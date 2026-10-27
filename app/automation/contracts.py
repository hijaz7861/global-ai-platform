"""Validate the narrow MCP contract before any engine adapter call.

This module does not connect to the engine or authorize workflow execution. The
platform must derive actor and organization scope from its authenticated session.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmptyInput(StrictInput):
    pass


class WorkflowRefInput(StrictInput):
    workflowFile: str = Field(min_length=1, max_length=120)

    @field_validator("workflowFile")
    @classmethod
    def validate_workflow_path(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"workflows/[A-Za-z0-9._-]+\.json", value):
            raise ValueError("workflowFile must name a JSON file directly inside workflows/")
        if value in {"workflows/..json", "workflows/.json"}:
            raise ValueError("invalid workflowFile")
        return value


class WorkflowGoalInput(WorkflowRefInput):
    goal: str = Field(min_length=1, max_length=2000)

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value.strip()


class OptionalWorkflowRefInput(StrictInput):
    workflowFile: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("workflowFile")
    @classmethod
    def validate_workflow_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return WorkflowRefInput(workflowFile=value).workflowFile


# This allowlist intentionally excludes workflow.apply and workflow.execute.
_TOOL_INPUTS: dict[str, type[BaseModel]] = {
    "workflow.list": EmptyInput,
    "workflow.inspect": WorkflowRefInput,
    "workflow.design": WorkflowGoalInput,
    "workflow.proposals": OptionalWorkflowRefInput,
    "workflow.versions": OptionalWorkflowRefInput,
    "workflow.propose": WorkflowGoalInput,
}


def validate_mcp_tool_call(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return normalized arguments for an explicitly allowed MCP tool.

    Approval flags and other unexpected fields are rejected by strict schemas.
    This validates shape only; authorization, tenant scope, approval, transport,
    and audit logging remain responsibilities of the future adapter.
    """
    if tool_name not in _TOOL_INPUTS:
        raise ValueError("MCP tool is not permitted by the platform integration policy")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return _TOOL_INPUTS[tool_name].model_validate(arguments).model_dump(exclude_none=True)
