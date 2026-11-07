"""Strict input and decision schemas for the constrained agent loop."""
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentTaskInput(BaseModel):
    goal: str = Field(min_length=1, max_length=5_000)
    model_route: Literal["primary", "fallback"] = "primary"
    task_type: Literal["chat", "summarize", "analyze", "extract"] = "analyze"
    data_class: Literal["public", "internal"] = "internal"
    max_steps: int = Field(default=3, ge=1, le=5)
    max_output_tokens: int = Field(default=256, ge=32, le=512)

    @field_validator("goal")
    @classmethod
    def normalize_goal(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent goal must not be blank")
        return normalized


class AgentDecision(BaseModel):
    action: Literal["tool", "final"]
    tool: str | None = Field(default=None, min_length=2, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    answer: str | None = Field(default=None, max_length=5_000)

    model_config = ConfigDict(extra="forbid")

    @field_validator("arguments")
    @classmethod
    def bound_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 8_192:
            raise ValueError("Agent tool arguments exceed 8192 bytes")
        return value

    @model_validator(mode="after")
    def validate_action_shape(self):
        if self.action == "tool":
            if not self.tool or self.answer is not None:
                raise ValueError("Tool decisions require a tool name and must not contain a final answer")
        elif not self.answer or not self.answer.strip() or self.tool is not None or self.arguments:
            raise ValueError("Final decisions require an answer and must not contain tool arguments")
        return self
