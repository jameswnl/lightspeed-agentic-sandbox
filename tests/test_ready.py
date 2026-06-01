"""Tests for GET /ready readiness endpoint."""

from __future__ import annotations

import urllib.error
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lightspeed_agentic.health import (
    check_provider_endpoint,
    check_provider_env,
    probe_provider_endpoint,
    register_ready_route,
    run_readiness_checks,
)


def test_check_provider_env_provider_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIGHTSPEED_AGENT_PROVIDER", raising=False)
    assert check_provider_env() == "error: LIGHTSPEED_AGENT_PROVIDER not set"
    assert check_provider_endpoint() == "error: LIGHTSPEED_AGENT_PROVIDER not set"


def test_check_provider_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    assert "error: missing" in check_provider_env()


def test_check_provider_env_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert check_provider_env() == "ok"


def test_check_provider_env_claude_vertex_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/var/secrets/google/credentials.json")
    assert check_provider_env() == "ok"


def test_check_provider_env_gemini_vertex_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "gemini")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/var/secrets/google/credentials.json")
    assert check_provider_env() == "ok"


def test_check_provider_env_gemini_either_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "gemini")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert "error: missing" in check_provider_env()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert check_provider_env() == "ok"


def test_check_provider_env_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "watsonx")
    assert "unknown provider" in check_provider_env()


def test_probe_provider_endpoint_http_error_is_ok() -> None:
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("", 403, "", {}, None)):
        assert probe_provider_endpoint("https://api.anthropic.com/") == "ok"


def test_probe_provider_endpoint_connection_error() -> None:
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("connection refused")):
        result = probe_provider_endpoint("https://api.anthropic.com/")
    assert result.startswith("error: ")


def test_check_provider_endpoint_openai_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://custom.example/v1")
    with patch(
        "lightspeed_agentic.health.probe_provider_endpoint",
        return_value="ok",
    ) as mock_probe:
        assert check_provider_endpoint() == "ok"
    mock_probe.assert_called_once_with("https://custom.example/v1")


def test_check_provider_endpoint_claude_vertex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "claude")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("CLOUD_ML_REGION", "europe-west4")
    with patch(
        "lightspeed_agentic.health.probe_provider_endpoint",
        return_value="ok",
    ) as mock_probe:
        assert check_provider_endpoint() == "ok"
    mock_probe.assert_called_once_with("https://europe-west4-aiplatform.googleapis.com/")


def test_check_provider_endpoint_claude_vertex_default_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "claude")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
    with patch(
        "lightspeed_agentic.health.probe_provider_endpoint",
        return_value="ok",
    ) as mock_probe:
        assert check_provider_endpoint() == "ok"
    mock_probe.assert_called_once_with("https://us-east5-aiplatform.googleapis.com/")


def test_check_provider_endpoint_gemini_vertex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("CLOUD_ML_REGION", "us-central1")
    with patch(
        "lightspeed_agentic.health.probe_provider_endpoint",
        return_value="ok",
    ) as mock_probe:
        assert check_provider_endpoint() == "ok"
    mock_probe.assert_called_once_with("https://us-central1-aiplatform.googleapis.com/")


@pytest.mark.asyncio
async def test_ready_route_all_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    app = FastAPI()
    register_ready_route(app)
    with patch(
        "lightspeed_agentic.health.check_provider_endpoint",
        return_value="ok",
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_route_provider_env_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = FastAPI()
    register_ready_route(app)
    with patch(
        "lightspeed_agentic.health.check_provider_endpoint",
        return_value="ok",
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "error"
    assert body["checks"]["provider_env"].startswith("error: ")
    assert body["checks"]["provider_endpoint"] == "ok"


def test_run_readiness_checks_all_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTSPEED_AGENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    with (
        patch("lightspeed_agentic.health.check_provider_env", return_value="ok"),
        patch("lightspeed_agentic.health.check_provider_endpoint", return_value="ok"),
    ):
        ok, checks = run_readiness_checks()
    assert ok is True
    assert checks == {"provider_env": "ok", "provider_endpoint": "ok"}
