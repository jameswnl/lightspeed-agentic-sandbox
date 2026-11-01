"""Tests for SDK workarounds shared by app.py and the step CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from openai.types.responses.response_usage import InputTokensDetails

from lightspeed_agentic.sdk_patches import apply_sdk_patches

REPO_ROOT = Path(__file__).resolve().parent.parent

needs_cache_write_tokens = pytest.mark.skipif(
    "cache_write_tokens" not in InputTokensDetails.model_fields,
    reason="installed openai has no InputTokensDetails.cache_write_tokens",
)


def build_usage_without_cache_write_tokens() -> None:
    """Build openai usage details the way callers that predate cache_write_tokens do."""
    from agents.usage import InputTokensDetails, Usage
    from openai.types.responses import ResponseUsage
    from pydantic import TypeAdapter

    InputTokensDetails(cached_tokens=0)
    InputTokensDetails.model_validate({"cached_tokens": 0})
    TypeAdapter(InputTokensDetails).validate_python({"cached_tokens": 0})
    Usage()
    Usage(input_tokens_details={"cached_tokens": 0})
    ResponseUsage.model_validate(
        {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    )


def _run_python(code: str, **env: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-P", "-c", code],
        capture_output=True,
        cwd="/",
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), **env},
        timeout=60,
        check=False,
    )


def test_app_import_applies_patch():
    proc = _run_python(
        "import lightspeed_agentic.app\n"
        "from tests.test_sdk_patches import build_usage_without_cache_write_tokens\n"
        "build_usage_without_cache_write_tokens()\n",
        LIGHTSPEED_PROVIDER="openai",
        OPENAI_API_KEY="test-key",
    )
    assert proc.returncode == 0, proc.stderr.decode()


@needs_cache_write_tokens
def test_patch_works_after_schemas_were_built():
    proc = _run_python(
        "from agents.usage import InputTokensDetails, Usage\n"
        "from openai.types.responses import ResponseUsage\n"
        "InputTokensDetails(cached_tokens=0, cache_write_tokens=0)\n"
        "Usage()\n"
        "ResponseUsage.model_validate({'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2,"
        " 'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},"
        " 'output_tokens_details': {'reasoning_tokens': 0}})\n"
        "from lightspeed_agentic.sdk_patches import apply_sdk_patches\n"
        "apply_sdk_patches()\n"
        "apply_sdk_patches()\n"
        "from tests.test_sdk_patches import build_usage_without_cache_write_tokens\n"
        "build_usage_without_cache_write_tokens()\n"
    )
    assert proc.returncode == 0, proc.stderr.decode()


@needs_cache_write_tokens
def test_patch_leaves_existing_default_alone():
    field = InputTokensDetails.model_fields["cache_write_tokens"]
    original = field.default
    field.default = 7
    try:
        apply_sdk_patches()
        assert field.default == 7
    finally:
        field.default = original
