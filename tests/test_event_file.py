"""Tests for JSONL event file sink in EventLogger."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lightspeed_agentic.logging import EventLogger
from lightspeed_agentic.types import (
    ContentBlockStopEvent,
    ResultEvent,
    ThinkingDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class TestEventFileWritesJsonl:
    """EventLogger writes structured JSONL to the configured event file."""

    def test_tool_call_event_written_as_jsonl(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ToolCallEvent(name="kubectl", input="get pods"))
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "tool_call"
        assert record["data"]["name"] == "kubectl"
        assert record["data"]["input"] == "get pods"
        assert "ts" in record

    def test_tool_result_event_written_as_jsonl(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ToolResultEvent(output="NAME  READY  STATUS\npod1  1/1    Running"))
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "tool_result"
        assert "pod1" in record["data"]["output"]

    def test_thinking_event_written_on_flush(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ThinkingDeltaEvent(thinking="Let me think about this..."))
            el.log(ContentBlockStopEvent())
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "thinking"
        assert "think about this" in record["data"]["text"]

    def test_result_event_written_as_jsonl(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ResultEvent(text="done", cost_usd=0.05, input_tokens=100, output_tokens=50))
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "result"
        assert record["data"]["text"] == "done"

    def test_error_event_written_on_exception(self, tmp_path: Path) -> None:
        """EventLogger.log_error writes an error event to the file."""
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log_error("something went wrong")
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "error"
        assert record["data"]["message"] == "something went wrong"


class TestEventFileJsonStructure:
    """Each event type produces the correct JSON structure."""

    def test_tool_call_structure(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ToolCallEvent(name="bash", input="ls -la"))
        record = json.loads(log_path.read_text().strip())
        assert set(record.keys()) == {"ts", "type", "data"}
        assert set(record["data"].keys()) == {"name", "input"}

    def test_tool_result_structure(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ToolResultEvent(output="file1.txt"))
        record = json.loads(log_path.read_text().strip())
        assert set(record.keys()) == {"ts", "type", "data"}
        assert set(record["data"].keys()) == {"output"}

    def test_thinking_structure(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ThinkingDeltaEvent(thinking="hmm"))
            el.log(ContentBlockStopEvent())
        record = json.loads(log_path.read_text().strip())
        assert set(record.keys()) == {"ts", "type", "data"}
        assert set(record["data"].keys()) == {"text"}

    def test_result_structure_includes_cost_and_tokens(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ResultEvent(text="ok", cost_usd=0.123, input_tokens=500, output_tokens=200))
        record = json.loads(log_path.read_text().strip())
        assert record["data"]["cost_usd"] == 0.123
        assert record["data"]["input_tokens"] == 500
        assert record["data"]["output_tokens"] == 200
        assert record["data"]["text"] == "ok"

    def test_error_structure(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log_error("bad thing")
        record = json.loads(log_path.read_text().strip())
        assert set(record.keys()) == {"ts", "type", "data"}
        assert set(record["data"].keys()) == {"message"}


class TestEventFileGracefulFallback:
    """File sink is skipped gracefully when the file cannot be opened."""

    def test_readonly_path_skips_file_output(self, tmp_path: Path) -> None:
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        log_path = readonly_dir / "events.jsonl"
        try:
            with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
                el = EventLogger("test")
                # Should not raise
                el.log(ToolCallEvent(name="test", input="x"))
            # File should not have been created (dir is read-only)
            readonly_dir.chmod(0o755)
            assert not log_path.exists()
        finally:
            readonly_dir.chmod(0o755)

    def test_no_env_var_skips_file_output(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.copy()
            env.pop("AGENT_EVENT_LOG", None)
            with patch.dict(os.environ, env, clear=True):
                el = EventLogger("test")
                # Should not raise
                el.log(ToolCallEvent(name="test", input="x"))


class TestEventFileLineBuffering:
    """File sink flushes after each write for real-time tail -F streaming."""

    def test_flush_after_each_event(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ToolCallEvent(name="cmd1", input="a"))
            # Read immediately -- should already be flushed to disk
            lines_after_first = log_path.read_text().strip().splitlines()
            assert len(lines_after_first) == 1

            el.log(ToolCallEvent(name="cmd2", input="b"))
            lines_after_second = log_path.read_text().strip().splitlines()
            assert len(lines_after_second) == 2


class TestEventFileTruncation:
    """Tool inputs/outputs are truncated in JSONL events."""

    def test_tool_call_input_truncated_at_2000_chars(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        long_input = "x" * 3000
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ToolCallEvent(name="test", input=long_input))
        record = json.loads(log_path.read_text().strip())
        assert len(record["data"]["input"]) == 2000

    def test_tool_result_output_truncated_at_2000_chars(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        long_output = "y" * 3000
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ToolResultEvent(output=long_output))
        record = json.loads(log_path.read_text().strip())
        assert len(record["data"]["output"]) == 2000


class TestEventFileClose:
    """EventLogger.close() releases the file handle."""

    def test_close_releases_file_handle(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ToolCallEvent(name="cmd", input="x"))
            assert el._event_file is not None
            el.close()
            assert el._event_file is None

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.close()
            el.close()  # should not raise

    def test_writes_after_close_are_silently_skipped(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.close()
            el.log(ToolCallEvent(name="cmd", input="x"))
        # File may exist but should be empty (opened then closed before write)
        content = log_path.read_text() if log_path.exists() else ""
        assert content.strip() == ""


class TestEventFileEdgeCases:
    """Edge cases: whitespace-only thinking, empty result text."""

    def test_whitespace_only_thinking_produces_no_event(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ThinkingDeltaEvent(thinking="   "))
            el.log(ContentBlockStopEvent())
        content = log_path.read_text() if log_path.exists() else ""
        assert content.strip() == ""

    def test_thinking_text_truncated_at_2000_chars(self, tmp_path: Path) -> None:
        log_path = tmp_path / "events.jsonl"
        long_thinking = "t" * 3000
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            el.log(ThinkingDeltaEvent(thinking=long_thinking))
            el.log(ContentBlockStopEvent())
        record = json.loads(log_path.read_text().strip())
        assert len(record["data"]["text"]) == 2000


class TestExistingStderrLoggingPreserved:
    """Adding the file sink does not break existing stderr logging."""

    def test_stderr_logging_still_works(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        log_path = tmp_path / "events.jsonl"
        with patch.dict(os.environ, {"AGENT_EVENT_LOG": str(log_path)}):
            el = EventLogger("test")
            with caplog.at_level("INFO", logger="lightspeed_agentic"):
                el.log(ToolCallEvent(name="kubectl", input="get pods"))
        assert "kubectl" in caplog.text

    def test_stderr_logging_works_without_file_sink(self, caplog: pytest.LogCaptureFixture) -> None:
        env = os.environ.copy()
        env.pop("AGENT_EVENT_LOG", None)
        with patch.dict(os.environ, env, clear=True):
            el = EventLogger("test")
            with caplog.at_level("INFO", logger="lightspeed_agentic"):
                el.log(ToolCallEvent(name="kubectl", input="get pods"))
        assert "kubectl" in caplog.text
