"""Policy-bound executor for statically registered read-only tools."""
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.tools.registry import TOOL_REGISTRY, ToolContext
from app.audit import record_audit_event


class ToolNotFound(LookupError):
    """Tool is not in the static registry."""


class ToolInputError(ValueError):
    """Tool arguments do not match the registered schema."""


class ToolExecutionFailed(RuntimeError):
    """A registered read-only tool failed without exposing raw internal details."""


def list_available_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "capability": spec.capability,
            "risk": "read_only",
            "input_schema": spec.input_model.model_json_schema(),
        }
        for spec in sorted(TOOL_REGISTRY.values(), key=lambda item: item.name)
    ]


def execute_tool(
    db: Session,
    *,
    actor_user_id: str,
    organization_id: str,
    role: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    spec = TOOL_REGISTRY.get(tool_name)
    if spec is None:
        raise ToolNotFound("Tool is not registered")
    try:
        validated = spec.input_model.model_validate(arguments)
    except ValidationError as exc:
        raise ToolInputError("Tool arguments failed schema validation") from exc

    context = ToolContext(
        db=db,
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        role=role,
    )
    try:
        result = spec.handler(context, validated)
        if not isinstance(result, dict):
            raise TypeError("Tool handler returned a non-object result")
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            action="ai.tool.executed",
            entity_type="ai_tool",
            entity_id=tool_name,
            details={"status": "success", "role": role},
        )
        db.commit()
        return result
    except LookupError:
        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            action="ai.tool.denied",
            entity_type="ai_tool",
            entity_id=tool_name,
            details={"status": "not_found", "role": role},
        )
        db.commit()
        raise ToolNotFound("Tool resource not found in the caller's scope") from None
    except Exception as exc:
        db.rollback()
        raise ToolExecutionFailed("Registered tool execution failed") from exc
