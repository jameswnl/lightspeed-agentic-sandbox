"""Tests for FastAPI routes using mock providers."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lightspeed_agentic.routes import _resolve_router_model, build_router, resolve_startup_model
from lightspeed_agentic.routes.query import _format_context_prefix
from lightspeed_agentic.types import (
    ContentBlockStopEvent,
    ResultEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
)

from .conftest import MockProvider


def _make_app(provider) -> FastAPI:
    app = FastAPI()
    router = build_router(provider, skills_dir="/workspace", model="test-model")
    app.include_router(router, prefix="/v1/agent")
    return app


def _make_audit_app(provider) -> FastAPI:
    app = FastAPI()
    router = build_router(provider, skills_dir="/workspace", model="test-model", audit_enabled=True)
    app.include_router(router, prefix="/v1/agent")
    return app


@pytest.mark.asyncio
async def test_run_endpoint():
    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/agent/run",
            json={"query": "Diagnose the issue"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "mock result" in data["summary"]


@pytest.mark.asyncio
async def test_run_with_system_prompt():
    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/agent/run",
            json={
                "query": "Diagnose the issue",
                "systemPrompt": "You are an SRE agent.",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_run_with_context():
    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/agent/run",
            json={
                "query": "Diagnose the issue",
                "context": {
                    "targetNamespaces": ["default", "kube-system"],
                    "attempt": 2,
                    "previousAttempts": [{"attempt": 1, "failureReason": "timeout"}],
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_run_with_output_schema():
    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/agent/run",
            json={
                "query": "Diagnose",
                "outputSchema": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                },
            },
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_run_with_timeout_applied():
    """Verify timeout_ms is actually used: a slow provider exceeds a 1ms timeout."""
    import asyncio

    class SlowProvider(MockProvider):
        async def query(self, _options):
            await asyncio.sleep(0.1)
            async for event in super().query(_options):
                yield event

    app = _make_app(SlowProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/agent/run",
            json={"query": "test", "timeout_ms": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "timed out" in data["summary"].lower()


@pytest.mark.asyncio
async def test_run_with_timeout_default():
    """Without timeout_ms the server default applies and the fast mock succeeds."""
    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/agent/run",
            json={"query": "Diagnose"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_run_empty_response():
    provider = MockProvider(events=[ResultEvent(text="")])
    app = _make_app(provider)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/agent/run", json={"query": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "empty" in data["summary"].lower()


@pytest.mark.asyncio
async def test_run_text_response():
    provider = MockProvider(events=[ResultEvent(text="Just plain text, not JSON")])
    app = _make_app(provider)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/agent/run", json={"query": "test"})
        data = resp.json()
        assert data["success"] is True
        assert data["summary"] == "Just plain text, not JSON"


@pytest.mark.asyncio
async def test_run_accepts_traceparent_header():
    """Verify traceparent header is accepted and request succeeds."""
    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/agent/run",
            json={"query": "Diagnose the issue"},
            headers={"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_run_works_without_traceparent():
    """Without traceparent, a fresh trace ID is generated — request still succeeds."""
    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/agent/run",
            json={"query": "Diagnose the issue"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


def test_format_context_envelope_markers_only() -> None:
    """Rule 12: block starts and ends with fixed marker lines."""
    text = _format_context_prefix({})
    assert text == "[context]\n[/context]"


def test_format_context_unknown_keys_ignored() -> None:
    text = _format_context_prefix({"workflowPhase": "diagnose"})
    assert text == "[context]\n[/context]"


def test_format_context_target_namespaces() -> None:
    """Rule 13: comma-separated namespace list."""
    text = _format_context_prefix({"targetNamespaces": ["default", "kube-system"]})
    assert "Target namespaces: default, kube-system" in text
    assert text.startswith("[context]")
    assert text.endswith("[/context]")


def test_format_context_target_namespaces_empty_list_omitted() -> None:
    text = _format_context_prefix({"targetNamespaces": []})
    assert "Target namespaces:" not in text


def test_format_context_attempt_includes_of_max_literal() -> None:
    """Rule 14: attempt line uses literal 'of max' placeholder."""
    text = _format_context_prefix({"attempt": 2})
    assert "Attempt: 2 of max" in text


def test_format_context_attempt_zero_included() -> None:
    text = _format_context_prefix({"attempt": 0})
    assert "Attempt: 0 of max" in text


def test_format_context_previous_attempts_with_failure_reason() -> None:
    """Rule 15: header plus bullet lines with optional failureReason."""
    text = _format_context_prefix(
        {
            "previousAttempts": [
                {"attempt": 1, "failureReason": "timeout"},
                {"attempt": 2},
            ]
        }
    )
    assert "Previous attempts:" in text
    assert "  Attempt 1: timeout" in text
    assert "  Attempt 2" in text
    assert "  Attempt 2:" not in text


def test_format_context_previous_attempts_empty_list_omitted() -> None:
    text = _format_context_prefix({"previousAttempts": []})
    assert "Previous attempts:" not in text


def test_format_context_approved_option_with_actions() -> None:
    """Rule 16: remediation banners, fields, and action list."""
    text = _format_context_prefix(
        {
            "approvedOption": {
                "title": "Restart deployment",
                "diagnosis": {"rootCause": "CrashLoopBackOff"},
                "proposal": {
                    "description": "Roll out restart",
                    "risk": "low",
                    "reversible": True,
                    "actions": [
                        {"type": "patch", "description": "Scale to zero then up"},
                    ],
                },
            }
        }
    )
    assert "=== APPROVED REMEDIATION (execute ONLY these actions) ===" in text
    assert "Title: Restart deployment" in text
    assert "Diagnosis: CrashLoopBackOff" in text
    assert "Plan: Roll out restart" in text
    assert "Risk: low, Reversible: True" in text
    assert "Actions to execute:" in text
    assert "  - [patch] Scale to zero then up" in text
    assert "=== DO NOT perform any actions beyond what is listed above ===" in text


def test_format_context_approved_option_without_actions() -> None:
    text = _format_context_prefix(
        {
            "approvedOption": {
                "title": "Observe",
                "diagnosis": {"rootCause": "Unknown"},
                "proposal": {
                    "description": "Wait and collect logs",
                    "risk": "none",
                    "reversible": True,
                },
            }
        }
    )
    assert "Title: Observe" in text
    assert "Actions to execute:" not in text
    assert "=== DO NOT perform any actions beyond what is listed above ===" in text


def test_format_context_combined_fields() -> None:
    text = _format_context_prefix(
        {
            "targetNamespaces": ["openshift-logging"],
            "attempt": 3,
            "previousAttempts": [{"attempt": 2, "failureReason": "denied"}],
            "approvedOption": {
                "title": "Fix RBAC",
                "diagnosis": {"rootCause": "missing role"},
                "proposal": {
                    "description": "Apply RoleBinding",
                    "risk": "medium",
                    "reversible": False,
                },
            },
        }
    )
    lines = text.splitlines()
    assert lines[0] == "[context]"
    assert lines[-1] == "[/context]"
    assert "Target namespaces: openshift-logging" in text
    assert "Attempt: 3 of max" in text
    assert "  Attempt 2: denied" in text
    assert "Title: Fix RBAC" in text


def test_resolve_router_model_prefers_explicit_model() -> None:
    assert _resolve_router_model("openai", "custom-model") == "custom-model"


def test_resolve_startup_model_prefers_lightspeed_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIGHTSPEED_MODEL", "gpt-5-mini")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1")
    assert resolve_startup_model("openai") == "gpt-5-mini"


def test_resolve_startup_model_uses_sdk_env_when_lightspeed_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LIGHTSPEED_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1")
    assert resolve_startup_model("openai") == "gpt-4.1"


def test_resolve_startup_model_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LIGHTSPEED_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert resolve_startup_model("openai") is None


def test_resolve_router_model_prefers_lightspeed_when_both_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIGHTSPEED_MODEL", "gpt-5-mini")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1")
    assert _resolve_router_model("openai") == "gpt-5-mini"


def test_resolve_router_model_uses_sdk_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIGHTSPEED_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1")
    assert _resolve_router_model("openai") == "gpt-4.1"


def test_resolve_router_model_falls_back_to_lightspeed_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("LIGHTSPEED_MODEL", "gpt-5-mini")
    assert _resolve_router_model("openai") == "gpt-5-mini"


def test_resolve_router_model_uses_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightspeed_agentic.types import DEFAULT_MODEL

    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("LIGHTSPEED_MODEL", raising=False)
    assert _resolve_router_model("openai") == DEFAULT_MODEL


@pytest.mark.asyncio
async def test_run_emits_audit_events_when_enabled(capsys: pytest.CaptureFixture[str]):
    """When audit is enabled, started and completed events are emitted."""
    app = _make_audit_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/agent/run", json={"query": "test"})
        assert resp.status_code == 200
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.strip().splitlines() if line.strip().startswith("{")]
    event_types = [e["event"] for e in events]
    assert "audit.agent.started" in event_types
    assert "audit.agent.completed" in event_types
    for e in events:
        assert e["level"] == "audit"
        assert "trace_id" in e
        assert "phase" in e


@pytest.mark.asyncio
async def test_run_no_audit_events_when_disabled(capsys: pytest.CaptureFixture[str]):
    """Default (audit disabled) emits no audit events."""
    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/agent/run", json={"query": "test"})
        assert resp.status_code == 200
    out = capsys.readouterr().out
    audit_lines = [line for line in out.splitlines() if '"audit.agent.' in line]
    assert audit_lines == []


@pytest.mark.asyncio
async def test_run_audit_with_tool_events(capsys: pytest.CaptureFixture[str]):
    """Audit logger captures tool call and result events."""
    events_seq = [
        ToolCallEvent(name="bash", input="ls"),
        ToolResultEvent(output="file.txt"),
        ResultEvent(
            text='{"success": true, "summary": "done"}',
            cost_usd=0.01,
            input_tokens=10,
            output_tokens=5,
        ),
    ]
    app = _make_audit_app(MockProvider(events=events_seq))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/agent/run", json={"query": "test"})
        assert resp.status_code == 200
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.strip().splitlines() if line.strip().startswith("{")]
    event_types = [e["event"] for e in events]
    assert "audit.agent.tool.call" in event_types
    assert "audit.agent.tool.result" in event_types


@pytest.mark.asyncio
async def test_run_audit_with_text_buffering(capsys: pytest.CaptureFixture[str]):
    """Text deltas are buffered and emitted as audit.agent.text on block stop."""
    events_seq = [
        TextDeltaEvent(text="hello "),
        TextDeltaEvent(text="world"),
        ContentBlockStopEvent(),
        ResultEvent(
            text='{"success": true, "summary": "done"}', cost_usd=0, input_tokens=0, output_tokens=0
        ),
    ]
    app = _make_audit_app(MockProvider(events=events_seq))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/agent/run", json={"query": "test"})
        assert resp.status_code == 200
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.strip().splitlines() if line.strip().startswith("{")]
    text_events = [e for e in events if e["event"] == "audit.agent.text"]
    assert len(text_events) == 1
    assert text_events[0]["text"] == "hello world"


@pytest.mark.asyncio
async def test_run_audit_completed_captures_token_counts(capsys: pytest.CaptureFixture[str]):
    """Completed event carries input_tokens and output_tokens from ResultEvent."""
    events_seq = [
        ResultEvent(
            text='{"success": true, "summary": "done"}',
            cost_usd=0.05,
            input_tokens=42,
            output_tokens=17,
        ),
    ]
    app = _make_audit_app(MockProvider(events=events_seq))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/agent/run", json={"query": "test"})
        assert resp.status_code == 200
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.strip().splitlines() if line.strip().startswith("{")]
    completed = [e for e in events if e["event"] == "audit.agent.completed"]
    assert len(completed) == 1
    assert completed[0]["input_tokens"] == 42
    assert completed[0]["output_tokens"] == 17
    assert completed[0]["cost_usd"] == 0.05


@pytest.mark.asyncio
async def test_run_audit_phase_derivation(capsys: pytest.CaptureFixture[str]):
    """Phase is derived from context fields."""
    app = _make_audit_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/agent/run",
            json={
                "query": "test",
                "context": {
                    "approvedOption": {
                        "title": "fix",
                        "diagnosis": {"rootCause": "test"},
                        "proposal": {"description": "test", "risk": "low", "reversible": True},
                    }
                },
            },
        )
        assert resp.status_code == 200
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.strip().splitlines() if line.strip().startswith("{")]
    for e in events:
        assert e["phase"] == "execution"
