"""E2E fixtures — single provider per process (no parametrization)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.runner import RunHttpResult, run_query
from steps.given import *  # noqa: F403 — step fixtures must be in conftest namespace
from steps.when import *  # noqa: F403
from steps.then import *  # noqa: F403


@pytest.fixture
def bdd_context() -> dict[str, Any]:
    return {}


@pytest.fixture(scope="session")
def server_url() -> str:
    url = os.environ.get("SANDBOX_SERVICE_URL", "").strip()
    if not url:
        pytest.fail("SANDBOX_SERVICE_URL is not set (use scripts/e2e-containers.sh or export it)")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def provider_name() -> str:
    name = os.environ.get("E2E_PROVIDER", "").strip()
    if not name:
        pytest.fail("E2E_PROVIDER is not set (e2e-containers.sh exports it)")
    return name


@pytest.fixture
def e2e_output_dir() -> Path | None:
    """Host-side output directory where skill tools write token files."""
    raw = os.environ.get("E2E_OUTPUT_DIR", "").strip()
    if not raw:
        return None
    return Path(raw)


@pytest.fixture
def run_runner(server_url: str) -> Callable[..., RunHttpResult]:
    def _run(
        query: str,
        *,
        system_prompt: str = "You are a helpful assistant. Follow instructions exactly.",
        output_schema: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> RunHttpResult:
        return run_query(
            server_url,
            query,
            system_prompt=system_prompt,
            output_schema=output_schema,
            context=context,
            timeout_ms=timeout_ms,
        )

    return _run
