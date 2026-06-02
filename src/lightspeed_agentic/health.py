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


def health_payload() -> dict[str, str]:
    return {"status": "ok"}


def register_health_routes(app: FastAPI) -> None:
    """Register GET /health (liveness)."""

    @app.get("/health")
    def health() -> dict[str, str]:
        return health_payload()


def check_provider_env(provider: str | None) -> str:
    """R1: required credential env var(s) present and non-empty."""
    if provider is None:
        return "error: provider not configured"
    env_vars = _PROVIDER_CREDENTIAL_VARS.get(provider)
    if env_vars is None:
        return f"error: unknown provider {provider!r}"
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


def check_provider_endpoint(provider: str | None) -> str:
    if provider is None:
        return "error: provider not configured"
    url_fn = _PROVIDER_PROBE_URL.get(provider)
    if url_fn is None:
        return f"error: unknown provider {provider!r}"
    url = url_fn().strip()
    if not url:
        return "error: empty probe URL"
    return probe_provider_endpoint(url)


def run_readiness_checks(provider: str | None) -> tuple[bool, dict[str, str]]:
    checks = {
        "provider_env": check_provider_env(provider),
        "provider_endpoint": check_provider_endpoint(provider),
    }
    return all(status == "ok" for status in checks.values()), checks


def ready_response(provider: str | None) -> tuple[int, dict[str, object]]:
    ok, checks = run_readiness_checks(provider)
    if ok:
        return 200, {"status": "ok"}
    return 503, {"status": "error", "checks": checks}


def register_ready_route(app: FastAPI, *, sdk_name: str | None = None) -> None:
    """Register GET /ready (readiness)."""

    @app.get("/ready")
    def ready() -> JSONResponse:
        status_code, body = ready_response(sdk_name)
        return JSONResponse(status_code=status_code, content=body)
