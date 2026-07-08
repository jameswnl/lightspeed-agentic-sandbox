"""Tests for GET /v1/agent/events endpoint."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lightspeed_agentic.routes import build_router

from .conftest import MockProvider


def _make_app(provider) -> FastAPI:
    app = FastAPI()
    router = build_router(provider, skills_dir="/workspace", model="test-model")
    app.include_router(router, prefix="/v1/agent")
    return app


@pytest.mark.asyncio
async def test_events_returns_jsonl_content(tmp_path, monkeypatch):
    """GET /events returns JSONL content when the event log file exists."""
    log_file = tmp_path / "agent-events.jsonl"
    log_file.write_text(
        '{"ts":"2026-07-06T00:00:00Z","type":"tool_call","data":{"name":"bash"}}\n'
        '{"ts":"2026-07-06T00:00:01Z","type":"result","data":{"text":"done"}}\n'
    )
    monkeypatch.setenv("AGENT_EVENT_LOG", str(log_file))

    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/agent/events")

    assert resp.status_code == 200
    assert resp.text == log_file.read_text()


@pytest.mark.asyncio
async def test_events_returns_ndjson_content_type(tmp_path, monkeypatch):
    """Response content type is application/x-ndjson."""
    log_file = tmp_path / "agent-events.jsonl"
    log_file.write_text('{"ts":"2026-07-06T00:00:00Z","type":"result","data":{}}\n')
    monkeypatch.setenv("AGENT_EVENT_LOG", str(log_file))

    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/agent/events")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-ndjson"


@pytest.mark.asyncio
async def test_events_returns_404_when_file_missing(tmp_path, monkeypatch):
    """GET /events returns 404 when no event log file exists yet."""
    log_file = tmp_path / "nonexistent.jsonl"
    monkeypatch.setenv("AGENT_EVENT_LOG", str(log_file))

    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/agent/events")

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_events_returns_404_when_env_var_unset(monkeypatch):
    """GET /events returns 404 when AGENT_EVENT_LOG is not configured."""
    monkeypatch.delenv("AGENT_EVENT_LOG", raising=False)

    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/agent/events")

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_events_returns_empty_body_for_empty_file(tmp_path, monkeypatch):
    """GET /events succeeds with empty body when log file exists but is empty."""
    log_file = tmp_path / "agent-events.jsonl"
    log_file.write_text("")
    monkeypatch.setenv("AGENT_EVENT_LOG", str(log_file))

    app = _make_app(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/agent/events")

    assert resp.status_code == 200
    assert resp.text == ""
