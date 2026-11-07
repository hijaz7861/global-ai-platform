"""Synchronous task-worker adapter for the asynchronous bounded agent loop."""
import asyncio

from app.ai.agents.runner import run_agent_task as run_agent_async
from app.ai.runtime.tasks import TaskExecutionContext


def run_agent_task(context: TaskExecutionContext) -> dict:
    """Execute an agent task from the standalone synchronous worker process."""
    return asyncio.run(run_agent_async(context))
