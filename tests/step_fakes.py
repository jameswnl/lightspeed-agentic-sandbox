"""Test-only entry point for the step CLI with fake providers.

Usage: python -P -m tests.step_fakes <scenario> [step CLI args...]

Runs the real ``lightspeed_agentic.step.main`` with a fake provider injected at
the provider boundary, so subprocess tests exercise the real CLI without
credentials. Not shipped in the image.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
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


class OpenAIUsageProvider(MockProvider):
    """Builds openai usage details without cache_write_tokens inside the CLI process."""

    async def query(self, options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        from tests.test_sdk_patches import build_usage_without_cache_write_tokens

        build_usage_without_cache_write_tokens()
        async for event in super().query(options):
            yield event


class PlantResultProvider(MockProvider):
    """Creates runDir/result.json mid-run, as a same-uid tool could."""

    async def query(self, options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        path = os.path.join(os.environ["STEP_FAKE_RUN_DIR"], "result.json")
        with open(path, "w") as f:  # noqa: ASYNC230
            f.write("planted")
        async for event in super().query(options):
            yield event


class ProcessStateProvider(MockProvider):
    """Fails the run unless stdin is /dev/null and (on Linux) the process is non-dumpable."""

    async def query(self, options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        stdin, devnull = os.fstat(0), os.stat(os.devnull)
        if (stdin.st_dev, stdin.st_ino) != (devnull.st_dev, devnull.st_ino):
            raise ValueError("stdin is not /dev/null")
        if sys.platform == "linux" and ctypes.CDLL(None).prctl(3, 0, 0, 0, 0) != 0:
            raise ValueError("process is dumpable")  # 3 = PR_GET_DUMPABLE
        async for event in super().query(options):
            yield event


class SlowPublishProvider(MockProvider):
    """Makes every later fsync block, so result publication hangs after the run."""

    async def query(self, options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        os.fsync = lambda _fd: time.sleep(3600)
        async for event in super().query(options):
            yield event


def _fill_stderr_later() -> None:
    time.sleep(0.5)
    os.set_blocking(2, False)
    with contextlib.suppress(BlockingIOError):
        while True:
            os.write(2, b"e" * 4096)
    os.set_blocking(2, True)


class FillStderrLaterProvider(_RaisingProvider):
    """Fails fast (no events), then fills stderr while the CLI waits to deliver the result."""

    def __init__(self) -> None:
        super().__init__(ValueError("boom"))

    async def query(self, options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        threading.Thread(target=_fill_stderr_later, daemon=True).start()
        async for event in super().query(options):
            yield event


def _fill_stderr_after_publish_then_block(run_dir: str) -> None:
    while not os.path.exists(os.path.join(run_dir, "result.json")):
        time.sleep(0.01)
    os.set_blocking(2, False)
    with contextlib.suppress(BlockingIOError):
        while True:
            os.write(2, b"e" * 4096)
    os.set_blocking(2, True)
    threading.Event().wait()


class MessyExitProvider(MockProvider):
    """Leaves buffered stdout, a stuck non-daemon thread and a full stderr at exit."""

    async def query(self, options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        print("buffered, never flushed", end="")
        run_dir = os.environ["STEP_FAKE_RUN_DIR"]
        threading.Thread(target=_fill_stderr_after_publish_then_block, args=(run_dir,)).start()
        async for event in super().query(options):
            yield event


class DumpableProvider(MockProvider):
    """Reports prctl(PR_GET_DUMPABLE) for the CLI process as the result summary (Linux only)."""

    async def query(self, _options: ProviderQueryOptions) -> AsyncIterator[ProviderEvent]:
        dumpable = ctypes.CDLL(None).prctl(3, 0, 0, 0, 0)  # 3 = PR_GET_DUMPABLE
        yield ResultEvent(text=f'{{"success": true, "summary": "dumpable={dumpable}"}}')


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
        case "huge_error":
            return _RaisingProvider(ValueError("x" * 1_000_000))
        case "nan":
            return MockProvider(
                [
                    ToolCallEvent(name="Bash", input="oc get pods"),
                    ResultEvent(
                        text='{"success": true, "summary": "s", "x": NaN}', cost_usd=float("nan")
                    ),
                ]
            )
        case "plant_result":
            return PlantResultProvider()
        case "process_state":
            return ProcessStateProvider()
        case "noisy":
            return NoisyProvider()
        case "openai_usage":
            return OpenAIUsageProvider()
        case "slow_publish":
            return SlowPublishProvider()
        case "fill_stderr_later":
            return FillStderrLaterProvider()
        case "messy_exit":
            return MessyExitProvider()
        case "keyboard_interrupt":
            return _RaisingProvider(KeyboardInterrupt())
        case "dumpable":
            return DumpableProvider()
        case "hang":
            return HangProvider()
        case "stubborn":
            return StubbornProvider()
    raise ValueError(f"unknown scenario: {scenario}")


if __name__ == "__main__":
    from lightspeed_agentic.step import RUN_ROOT, main

    if sys.argv[1] == "openai_usage":
        from tests.test_sdk_patches import simulate_required_cache_write_tokens

        simulate_required_cache_write_tokens()
    run_root = os.environ.get("STEP_FAKE_RUN_ROOT", RUN_ROOT)
    main(sys.argv[2:], provider=make_provider(sys.argv[1]), run_root=run_root)
