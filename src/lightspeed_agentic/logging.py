"""Normalized provider event logging — maps to lightspeed-agent/src/providers/logging.ts."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import IO, Any

from lightspeed_agentic.types import ProviderEvent

logger = logging.getLogger("lightspeed_agentic")

MAX_THINKING_LOG = 2000
MAX_TOOL_INPUT_LOG = 500
MAX_TOOL_OUTPUT_LOG = 1000
MAX_RESULT_LOG = 500
THINKING_BUF_FLUSH = 50_000

DEFAULT_EVENT_LOG_PATH = "/var/log/agent-events.jsonl"
MAX_EVENT_TOOL_INPUT = 2000
MAX_EVENT_TOOL_OUTPUT = 2000
MAX_EVENT_THINKING = 2000


class EventLogger:
    """Buffers thinking deltas and logs them as complete blocks.

    When the AGENT_EVENT_LOG env var is set (or defaults to
    /var/log/agent-events.jsonl), also writes structured JSONL events to that
    file for transcript persistence.  If the file cannot be opened, a warning
    is logged and file output is silently skipped.
    """

    def __init__(self, phase: str) -> None:
        self._phase = phase
        self._thinking_buf: list[str] = []
        self._thinking_len = 0
        self._event_file: IO[str] | None = None
        self._open_event_file()

    def _open_event_file(self) -> None:
        """Try to open the JSONL event log file."""
        path = os.environ.get("AGENT_EVENT_LOG")
        if path is None:
            return
        try:
            self._event_file = open(path, "a", encoding="utf-8")  # noqa: SIM115
        except OSError:
            logger.warning("Cannot open event log %s, skipping file output", path)

    def close(self) -> None:
        """Close the JSONL event log file if open."""
        if self._event_file is not None:
            self._event_file.close()
            self._event_file = None

    def _write_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Write a single JSONL event line and flush."""
        if self._event_file is None:
            return
        record = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "type": event_type,
            "data": data,
        }
        self._event_file.write(json.dumps(record) + "\n")
        self._event_file.flush()

    def _flush_thinking(self) -> None:
        if self._thinking_buf:
            text = "".join(self._thinking_buf).strip()
            if text:
                logger.info("[provider:%s] thinking: %s", self._phase, text[:MAX_THINKING_LOG])
                self._write_event("thinking", {"text": text[:MAX_EVENT_THINKING]})
            self._thinking_buf.clear()
            self._thinking_len = 0

    def log(self, event: ProviderEvent) -> None:
        """Log a provider event to stderr and optionally to the JSONL file."""
        match event.type:
            case "thinking_delta":
                self._thinking_buf.append(event.thinking)
                self._thinking_len += len(event.thinking)
                if self._thinking_len >= THINKING_BUF_FLUSH:
                    self._flush_thinking()
            case "content_block_stop":
                self._flush_thinking()
            case "tool_call":
                self._flush_thinking()
                logger.info(
                    "[provider:%s] tool_use: %s(%s)",
                    self._phase,
                    event.name,
                    event.input[:MAX_TOOL_INPUT_LOG],
                )
                self._write_event("tool_call", {
                    "name": event.name,
                    "input": event.input[:MAX_EVENT_TOOL_INPUT],
                })
            case "tool_result":
                logger.info(
                    "[provider:%s] tool_result: %s", self._phase, event.output[:MAX_TOOL_OUTPUT_LOG]
                )
                self._write_event("tool_result", {
                    "output": event.output[:MAX_EVENT_TOOL_OUTPUT],
                })
            case "result":
                self._flush_thinking()
                logger.info(
                    "[provider:%s] result: cost=$%.4f, tokens=%d",
                    self._phase,
                    event.cost_usd,
                    event.input_tokens + event.output_tokens,
                )
                if event.text:
                    logger.info(
                        "[provider:%s] output: %s", self._phase, event.text[:MAX_RESULT_LOG]
                    )
                self._write_event("result", {
                    "text": event.text,
                    "cost_usd": event.cost_usd,
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                })

    def log_error(self, message: str) -> None:
        """Log an error event to stderr and the JSONL file."""
        logger.error("[provider:%s] error: %s", self._phase, message)
        self._write_event("error", {"message": message})
