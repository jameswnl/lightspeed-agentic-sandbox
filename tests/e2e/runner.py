"""Synchronous HTTP client for E2E against the sandbox service."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RunHttpResult:
    status_code: int | None = None
    body: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    error: str | None = None
    latency_seconds: float = 0.0


def get_json(
    server_url: str,
    path: str,
    *,
    timeout_seconds: float = 30.0,
) -> RunHttpResult:
    """GET a path on the sandbox base URL (e.g. ``/health``, ``/ready``).

    Never raises for HTTP status — callers inspect ``status_code``.
    """
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/', got {path!r}")

    base = server_url.rstrip("/")
    result = RunHttpResult()
    start = time.monotonic()

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
            resp = client.get(f"{base}{path}")
    except httpx.RequestError as exc:
        result.error = str(exc)
        result.latency_seconds = time.monotonic() - start
        return result

    result.status_code = resp.status_code
    result.raw_text = resp.text
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            result.body = parsed
        else:
            result.body = {}
    except ValueError:
        result.body = {}

    result.latency_seconds = time.monotonic() - start
    return result


def run_query(
    server_url: str,
    query: str,
    *,
    system_prompt: str = "You are a helpful assistant. Follow instructions exactly.",
    output_schema: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    timeout_ms: int | None = None,
) -> RunHttpResult:
    """POST /v1/agent/run. Never raises for HTTP status — callers inspect status_code."""
    base = server_url.rstrip("/")
    result = RunHttpResult()
    start = time.monotonic()

    payload: dict[str, Any] = {
        "query": query,
        "systemPrompt": system_prompt,
    }
    if output_schema is not None:
        payload["outputSchema"] = output_schema
    if context is not None:
        payload["context"] = context
    if timeout_ms is not None:
        payload["timeout_ms"] = timeout_ms

    try:
        with httpx.Client(timeout=httpx.Timeout(300.0)) as client:
            resp = client.post(f"{base}/v1/agent/run", json=payload)
    except httpx.RequestError as exc:
        result.error = str(exc)
        result.latency_seconds = time.monotonic() - start
        return result

    result.status_code = resp.status_code
    result.raw_text = resp.text
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            result.body = parsed
        else:
            result.body = {}
    except ValueError:
        result.body = {}

    result.latency_seconds = time.monotonic() - start
    return result
