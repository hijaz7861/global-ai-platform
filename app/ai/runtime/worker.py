"""Bounded worker entrypoint and optional long-running polling loop."""
from __future__ import annotations

import argparse
from collections.abc import Callable
import time
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.ai.runtime.tasks import TaskExecutionContext, run_once
from app.db import SessionLocal

TaskHandler = Callable[[dict[str, Any]], dict[str, Any]]
ContextTaskHandler = Callable[[TaskExecutionContext], dict[str, Any]]


def _default_context_handlers() -> dict[str, ContextTaskHandler]:
    from app.ai.agents.worker import run_agent_task

    return {"agent.run": run_agent_task}


def run_worker_once(
    db: Session,
    *,
    worker_id: str,
    handlers: dict[str, TaskHandler] | None = None,
    context_handlers: dict[str, ContextTaskHandler] | None = None,
    lease_seconds: int = 300,
) -> bool:
    """Process at most one task using only explicitly registered Python handlers."""
    registered_context_handlers = (
        context_handlers if context_handlers is not None else _default_context_handlers()
    )
    return run_once(
        db,
        worker_id=worker_id,
        handlers=handlers,
        context_handlers=registered_context_handlers,
        lease_seconds=lease_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Global AI Platform durable task worker")
    parser.add_argument("--once", action="store_true", help="Process at most one task and exit")
    parser.add_argument("--worker-id", default=f"worker-{uuid4().hex[:12]}")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if not 0.25 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.25 and 60")
    if not 30 <= args.lease_seconds <= 3600:
        parser.error("--lease-seconds must be between 30 and 3600")

    handlers = _default_context_handlers()
    while True:
        with SessionLocal() as db:
            processed = run_worker_once(
                db,
                worker_id=args.worker_id,
                context_handlers=handlers,
                lease_seconds=args.lease_seconds,
            )
        if args.once:
            print("processed one task" if processed else "queue idle")
            return 0
        if not processed:
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
