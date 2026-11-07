"""Request/response contracts for provenance-aware AI memory."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateAIMemoryRequest(BaseModel):
    scope: Literal["user", "organization"] = "user"
    memory_type: Literal[
        "preference", "feedback", "project", "reference", "decision", "session_summary"
    ]
    content: str = Field(min_length=1, max_length=10_000)
    source_type: Literal["user_input", "manual", "task", "document", "tool"] = "user_input"
    source_ref: str | None = Field(default=None, max_length=256)
    confidence_percent: int = Field(default=50, ge=0, le=100)
    sensitivity: Literal["normal", "sensitive", "restricted"] = "normal"
    expires_at: datetime | None = None
    supersedes_id: str | None = Field(default=None, min_length=36, max_length=36)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Memory content must not be blank")
        return normalized


class AIMemoryResponse(BaseModel):
    id: str
    scope: str
    organization_id: str | None
    memory_type: str
    content: str
    content_sha256: str
    source_type: str
    source_ref: str | None
    confidence_percent: int
    sensitivity: str
    verification_status: str
    status: str
    supersedes_id: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
