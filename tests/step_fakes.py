"""Test-only entry point for the step CLI with fake providers.

Usage: python -P -m tests.step_fakes <scenario> [step CLI args...]

Runs the real ``lightspeed_agentic.step.main`` with a fake provider injected at
the provider boundary, so subprocess tests exercise the real CLI without
credentials. Not shipped in the image.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import subprocess
import sys
from collections.abc import AsyncIterator

from lightspeed_agentic.types import (
    AgentProvider,
    ProviderEvent,
    ProviderQueryOptions,
    ResultEvent,
    ToolCallEvent,
    ToolResultEvent,
)

from .conftest import MockProvider


class _RaisingProvider(MockProvider):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    async def query(self, _options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        raise self._exc
        yield  # pragma: no cover


class NoisyProvider(MockProvider):
    """Writes junk to stdout through every path a real provider might."""

    async def query(self, options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        from agents import enable_verbose_stdout_logging

        print("print junk")
        sys.stdout.flush()
        subprocess.run(["sh", "-c", "echo child junk"], check=True)  # noqa: ASYNC221, S607
        enable_verbose_stdout_logging()
        logging.getLogger("openai.agents").debug("verbose junk")
        async for event in super().query(options):
            yield event


class OpenAIUsageDefaultProvider(MockProvider):
    """Reports the openai-agents cache_write_tokens default seen inside the CLI process."""

    async def query(self, _options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        from agents.usage import InputTokensDetails

        default = InputTokensDetails.model_fields["cache_write_tokens"].default
        yield ResultEvent(text=json.dumps({"success": True, "summary": repr(default)}))


class HangProvider(MockProvider):
    async def query(self, _options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        await asyncio.sleep(3600)
        yield ResultEvent(text="never")


class StubbornProvider(MockProvider):
    """Swallows cancellation, so only the hard deadline can stop it."""

    async def query(self, _options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        while True:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(3600)
        yield  # pragma: no cover


def make_provider(scenario: str) -> AgentProvider:
    match scenario:
        case "ok":
            return MockProvider(
                [
                    ToolCallEvent(name="Bash", input="oc get pods"),
                    ToolResultEvent(output="pod-a Running"),
                    ResultEvent(
                        text='{"success": true, "summary": "mock result", "rootCause": "oom"}',
                        cost_usd=0.01,
                        input_tokens=100,
                        output_tokens=50,
                    ),
                ]
            )
        case "text":
            return MockProvider([ResultEvent(text="plain text answer")])
        case "empty":
            return MockProvider([ResultEvent(text="")])
        case "infra":
            return _RaisingProvider(ConnectionError("connection refused"))
        case "agent_error":
            return _RaisingProvider(ValueError("boom"))
        case "noisy":
            return NoisyProvider()
        case "openai_usage_default":
            return OpenAIUsageDefaultProvider()
        case "hang":
            return HangProvider()
        case "stubborn":
            return StubbornProvider()
    raise ValueError(f"unknown scenario: {scenario}")


if __name__ == "__main__":
    from lightspeed_agentic.step import main

    sys.exit(main(sys.argv[2:], provider=make_provider(sys.argv[1])))
