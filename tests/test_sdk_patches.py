"""Tests for SDK workarounds shared by app.py and the step CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from lightspeed_agentic.sdk_patches import apply_sdk_patches

REPO_ROOT = Path(__file__).resolve().parent.parent


def build_usage_without_cache_write_tokens() -> None:
    """Build openai usage details the way callers that predate cache_write_tokens do."""
    from agents.usage import InputTokensDetails, Usage
    from pydantic import TypeAdapter

    InputTokensDetails(cached_tokens=0)
    InputTokensDetails.model_validate({"cached_tokens": 0})
    TypeAdapter(InputTokensDetails).validate_python({"cached_tokens": 0})
    Usage()
    Usage(input_tokens_details={"cached_tokens": 0})


def test_app_import_applies_patch():
    code = (
        "import lightspeed_agentic.app\n"
        "from lightspeed_agentic.sdk_patches import apply_sdk_patches\n"
        "apply_sdk_patches()\n"
        "from tests.test_sdk_patches import build_usage_without_cache_write_tokens\n"
        "build_usage_without_cache_write_tokens()\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "LIGHTSPEED_PROVIDER": "openai",
        "OPENAI_API_KEY": "test-key",
    }
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-P", "-c", code],
        capture_output=True,
        cwd="/",
        env=env,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode()


def test_patch_leaves_existing_default_alone():
    from openai.types.responses.response_usage import InputTokensDetails

    field = InputTokensDetails.model_fields["cache_write_tokens"]
    original = field.default
    field.default = 7
    try:
        apply_sdk_patches()
        assert field.default == 7
    finally:
        field.default = original
