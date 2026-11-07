"""Schemas for durable AI task submissions and status inspection."""
from datetime import datetime
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateAITaskRequest(BaseModel):
    task_type: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    title: str = Field(min_length=2, max_length=200)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=10)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Task title must contain at least two non-space characters")
        return normalized

    @field_validator("input_payload")
    @classmethod
    def bound_json_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Task input must be finite JSON data") from exc
        if len(encoded.encode("utf-8")) > 32_768:
            raise ValueError("Task input exceeds 32768 bytes")
        return value


class AITaskResponse(BaseModel):
    id: str
    organization_id: str
    task_type: str
    title: str
    status: str
    attempt_count: int
    max_attempts: int
    result_payload: dict[str, Any] | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    cancel_requested_at: datetime | None
    created: bool = False

    model_config = ConfigDict(from_attributes=True)


class AITaskEventResponse(BaseModel):
    id: str
    event_type: str
    details: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
