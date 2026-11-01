"""Pydantic request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    query: str
    systemPrompt: str | None = None
    outputSchema: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    timeout_ms: int | None = None


class RunResponse(BaseModel, extra="allow"):
    success: bool
    summary: str


class StepRequest(RunRequest, extra="forbid"):
    """Step CLI stdin payload: RunRequest plus step identity and trace context."""

    timeout_ms: int | None = Field(default=None, le=86_400_000)  # 24h; bounds signal.alarm
    stepId: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    runDir: str
    traceparent: str | None = None
