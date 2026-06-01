"""Health and readiness probe handlers."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.responses import JSONResponse

PROBE_TIMEOUT_SEC = 3.0

# R1 — credential env vars (any listed var non-empty satisfies the check)
_PROVIDER_CREDENTIAL_VARS: dict[str, list[str]] = {
    "claude": ["ANTHROPIC_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"],
    "gemini": ["GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"],
    "openai": ["OPENAI_API_KEY"],
}


def _vertex_endpoint_url() -> str:
    region = os.environ.get("CLOUD_ML_REGION", "us-east5")
    return f"https://{region}-aiplatform.googleapis.com/"


def _claude_probe_url() -> str:
    if os.environ.get("CLAUDE_CODE_USE_VERTEX") == "1":
        return _vertex_endpoint_url()
    return "https://api.anthropic.com/"


def _gemini_probe_url() -> str:
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1"):
        return _vertex_endpoint_url()
    return "https://generativelanguage.googleapis.com/"


# R2 — unauthenticated reachability probe base URLs
_PROVIDER_PROBE_URL: dict[str, Callable[[], str]] = {
    "claude": _claude_probe_url,
    "gemini": _gemini_probe_url,
    "openai": lambda: os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/",
}


def _provider_name() -> str | None:
    """Provider from env; None if unset or blank (readiness treats that as not ready)."""
    raw = os.environ.get("LIGHTSPEED_AGENT_PROVIDER")
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower()


def health_payload() -> dict[str, str]:
    return {"status": "ok"}


def register_health_routes(app: FastAPI) -> None:
    """Register GET /health (liveness)."""

    @app.get("/health")
    def health() -> dict[str, str]:
        return health_payload()


def check_provider_env(provider: str | None = None) -> str:
    """R1: required credential env var(s) present and non-empty."""
    name = provider if provider is not None else _provider_name()
    if name is None:
        return "error: LIGHTSPEED_AGENT_PROVIDER not set"
    env_vars = _PROVIDER_CREDENTIAL_VARS.get(name)
    if env_vars is None:
        return f"error: unknown provider {name!r}"
    if any(os.environ.get(var, "").strip() for var in env_vars):
        return "ok"
    if len(env_vars) == 1:
        return f"error: missing {env_vars[0]}"
    return f"error: missing {' or '.join(env_vars)}"


def probe_provider_endpoint(url: str, timeout: float = PROBE_TIMEOUT_SEC) -> str:
    """R2: HTTP GET; any HTTP response (including 4xx) means reachable."""
    scheme = urlparse(url).scheme
    if scheme not in ("https", "http"):
        return f"error: unsupported URL scheme {scheme!r}"
    try:
        request = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310
            return "ok"
    except urllib.error.HTTPError:
        return "ok"
    except Exception as exc:
        return f"error: {exc}"


def check_provider_endpoint(provider: str | None = None) -> str:
    name = provider if provider is not None else _provider_name()
    if name is None:
        return "error: LIGHTSPEED_AGENT_PROVIDER not set"
    url_fn = _PROVIDER_PROBE_URL.get(name)
    if url_fn is None:
        return f"error: unknown provider {name!r}"
    url = url_fn().strip()
    if not url:
        return "error: empty probe URL"
    return probe_provider_endpoint(url)


def run_readiness_checks(provider: str | None = None) -> tuple[bool, dict[str, str]]:
    checks = {
        "provider_env": check_provider_env(provider),
        "provider_endpoint": check_provider_endpoint(provider),
    }
    return all(status == "ok" for status in checks.values()), checks


def ready_response() -> tuple[int, dict[str, object]]:
    ok, checks = run_readiness_checks()
    if ok:
        return 200, {"status": "ok"}
    return 503, {"status": "error", "checks": checks}


def register_ready_route(app: FastAPI) -> None:
    """Register GET /ready (readiness)."""

    @app.get("/ready")
    def ready() -> JSONResponse:
        status_code, body = ready_response()
        return JSONResponse(status_code=status_code, content=body)
