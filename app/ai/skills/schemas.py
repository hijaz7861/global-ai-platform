"""Schemas for tenant-owned, versioned instructional skills."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateAISkillRequest(BaseModel):
    skill_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9-]{1,63}$")
    version: str = Field(min_length=5, max_length=32, pattern=r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=50_000)
    source_type: Literal["internal", "user_authored", "imported"] = "user_authored"
    source_ref: str | None = Field(default=None, max_length=256)
    license_id: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9.+_-]{2,64}$")
    required_capabilities: list[str] = Field(default_factory=list, max_length=32)
    data_classes: list[Literal["public", "internal", "confidential", "restricted"]] = Field(default_factory=list, max_length=4)
    risk_level: Literal["low", "medium", "high", "critical"] = "low"

    @field_validator("title", "content")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be blank")
        return normalized


class AISkillResponse(BaseModel):
    id: str
    organization_id: str
    skill_id: str
    version: str
    title: str
    description: str
    content: str
    content_sha256: str
    source_type: str
    source_ref: str | None
    license_id: str
    required_capabilities: list[str]
    data_classes: list[str]
    risk_level: str
    status: str
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
