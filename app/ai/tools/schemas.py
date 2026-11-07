"""Strict input models for the static AI tool gateway."""
from pydantic import BaseModel, ConfigDict, Field


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemorySearchArgs(ToolArgs):
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=5, ge=1, le=10)


class TaskLookupArgs(ToolArgs):
    task_id: str = Field(min_length=36, max_length=36, pattern=r"^[0-9a-fA-F-]{36}$")
