"""Configuration mapping: LIGHTSPEED_* generic vars → SDK-specific env vars.

The operator sets generic LIGHTSPEED_* env vars on the sandbox pod.
This module maps them to the SDK-specific env vars that each provider
SDK reads internally. Called once at startup before provider construction.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

LLM_CREDENTIALS_PATH = "/var/run/secrets/llm-credentials"


def _setenv(key: str, value: str) -> None:
    os.environ[key] = value


def _setenv_if(key: str, value: str | None) -> None:
    if value:
        _setenv(key, value)


def _resolve_anthropic(model: str | None, url: str | None) -> str:
    _setenv_if("ANTHROPIC_MODEL", model)
    _setenv_if("ANTHROPIC_BASE_URL", url)
    return "claude"


def _resolve_vertex(
    model_provider: str | None,
    model: str | None,
    url: str | None,
    project: str | None,
    region: str | None,
) -> str:
    if not model_provider:
        raise ValueError(
            "LIGHTSPEED_MODEL_PROVIDER is required when LIGHTSPEED_PROVIDER=vertex"
        )

    match model_provider:
        case "Anthropic":
            _setenv_if("ANTHROPIC_MODEL", model)
            _setenv("CLAUDE_CODE_USE_VERTEX", "1")
            _setenv_if("ANTHROPIC_VERTEX_PROJECT_ID", project)
            _setenv_if("CLOUD_ML_REGION", region)
            _setenv(
                "GOOGLE_APPLICATION_CREDENTIALS",
                f"{LLM_CREDENTIALS_PATH}/GOOGLE_APPLICATION_CREDENTIALS",
            )
            _setenv_if("ANTHROPIC_BASE_URL", url)
            return "claude"
        case "Google":
            _setenv_if("GEMINI_MODEL", model)
            _setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
            _setenv_if("GOOGLE_CLOUD_PROJECT", project)
            _setenv_if("GOOGLE_CLOUD_LOCATION", region)
            _setenv(
                "GOOGLE_APPLICATION_CREDENTIALS",
                f"{LLM_CREDENTIALS_PATH}/GOOGLE_APPLICATION_CREDENTIALS",
            )
            return "gemini"
        case "OpenAI":
            _setenv_if("OPENAI_MODEL", model)
            _setenv_if("OPENAI_BASE_URL", url)
            return "openai"
        case _:
            raise ValueError(
                f"Unknown LIGHTSPEED_MODEL_PROVIDER: {model_provider!r}. "
                "Supported: Anthropic, Google, OpenAI"
            )


def _resolve_openai(model: str | None, url: str | None) -> str:
    _setenv_if("OPENAI_MODEL", model)
    _setenv_if("OPENAI_BASE_URL", url)
    return "openai"


def _resolve_azure(
    model: str | None,
    url: str | None,
    api_version: str | None,
) -> str:
    _setenv_if("OPENAI_MODEL", model)
    _setenv_if("AZURE_OPENAI_ENDPOINT", url)
    _setenv_if("AZURE_OPENAI_API_VERSION", api_version)
    return "openai"


def _resolve_bedrock(
    model: str | None,
    url: str | None,
    region: str | None,
) -> str:
    _setenv_if("ANTHROPIC_MODEL", model)
    _setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    _setenv_if("AWS_REGION", region)
    _setenv_if("ANTHROPIC_BASE_URL", url)
    return "claude"


def resolve_sdk() -> str:
    """Read LIGHTSPEED_* env vars, set SDK-specific env vars, return SDK name.

    Returns one of: "claude", "gemini", "openai".
    """
    provider = os.environ.get("LIGHTSPEED_PROVIDER", "").strip().lower() or "anthropic"
    model = os.environ.get("LIGHTSPEED_MODEL", "").strip() or None
    model_provider = os.environ.get("LIGHTSPEED_MODEL_PROVIDER", "").strip() or None
    url = os.environ.get("LIGHTSPEED_PROVIDER_URL", "").strip() or None
    project = os.environ.get("LIGHTSPEED_PROVIDER_PROJECT", "").strip() or None
    region = os.environ.get("LIGHTSPEED_PROVIDER_REGION", "").strip() or None
    api_version = os.environ.get("LIGHTSPEED_PROVIDER_API_VERSION", "").strip() or None

    match provider:
        case "anthropic":
            sdk = _resolve_anthropic(model, url)
        case "vertex":
            sdk = _resolve_vertex(model_provider, model, url, project, region)
        case "openai":
            sdk = _resolve_openai(model, url)
        case "azure":
            sdk = _resolve_azure(model, url, api_version)
        case "bedrock":
            sdk = _resolve_bedrock(model, url, region)
        case _:
            raise ValueError(
                f"Unknown provider: {provider!r}. "
                "Supported: anthropic, vertex, openai, azure, bedrock"
            )

    logger.info("Resolved LIGHTSPEED_PROVIDER=%s → SDK=%s", provider, sdk)
    return sdk
