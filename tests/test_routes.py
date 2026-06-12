"""Tests for FastAPI routes using mock providers."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lightspeed_agentic.routes import _resolve_router_model, build_router, resolve_startup_model
from lightspeed_agentic.routes.query import _format_context_prefix
from lightspeed_agentic.types import ResultEvent

from .conftest import MockProvider


def _make_app(provider) -> FastAPI:
    app = FastAPI()
    router = build_router(provider, skills_dir="/workspace", model="test-model")
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
