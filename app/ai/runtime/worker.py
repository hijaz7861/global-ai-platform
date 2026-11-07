"""Small worker entrypoint for registered, bounded task handlers."""
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.ai.runtime.tasks import run_once

TaskHandler = Callable[[dict[str, Any]], dict[str, Any]]


def run_worker_once(
    db: Session, *, worker_id: str, handlers: dict[str, TaskHandler], lease_seconds: int = 60,
) -> bool:
    """Process at most one task. No dynamic imports or model-generated code execution."""
    return run_once(db, worker_id=worker_id, handlers=handlers, lease_seconds=lease_seconds)
