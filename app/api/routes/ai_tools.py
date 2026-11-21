"""Read-only tool catalogue and execution API."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai.tools.gateway import (
    ToolExecutionFailed,
    ToolInputError,
    ToolNotFound,
    execute_tool,
    list_available_tools,
)
from app.api.deps import require_membership
from app.db import get_db
from app.models import Membership

router = APIRouter(tags=["ai-tools"])


@router.get("/organizations/{organization_id}/ai/tools")
def list_tools(
    organization_id: str,
    membership: Annotated[Membership, Depends(require_membership)],
) -> dict[str, Any]:
    return {"tools": list_available_tools(), "execution_mode": "read_only"}


@router.post("/organizations/{organization_id}/ai/tools/{tool_name}/execute")
def execute_registered_tool(
    organization_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    membership: Annotated[Membership, Depends(require_membership)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        result = execute_tool(
            db,
            actor_user_id=membership.user_id,
            organization_id=membership.organization_id,
            role=membership.role,
            tool_name=tool_name,
            arguments=arguments,
        )
    except ToolNotFound:
        raise HTTPException(status_code=404, detail="Tool or resource not found") from None
    except ToolInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ToolExecutionFailed:
        raise HTTPException(status_code=503, detail="Tool execution failed") from None
    return {"tool": tool_name, "result": result}
