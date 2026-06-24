"""Tests for audit event emission."""

from __future__ import annotations

import contextlib
import json
from typing import Any

import pytest

from lightspeed_agentic.audit import AuditLogger, derive_phase
from lightspeed_agentic.types import (
    ContentBlockStopEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class TestDerivePhase:
    def test_no_context_returns_analysis(self) -> None:
        assert derive_phase(None) == "analysis"

    def test_empty_context_returns_analysis(self) -> None:
        assert derive_phase({}) == "analysis"

    def test_approved_option_returns_execution(self) -> None:
        assert derive_phase({"approvedOption": {"title": "fix"}}) == "execution"

    def test_execution_result_returns_verification(self) -> None:
        assert derive_phase({"executionResult": {"success": True}}) == "verification"

    def test_explicit_phase_takes_precedence(self) -> None:
        assert (
            derive_phase({"phase": "escalation", "approvedOption": {"title": "fix"}})
            == "escalation"
        )

    def test_both_approved_and_result_prefers_verification(self) -> None:
        ctx: dict[str, Any] = {
            "approvedOption": {"title": "fix"},
            "executionResult": {"success": True},
        }
        assert derive_phase(ctx) == "verification"


def _collect_audit_lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    """Parse all JSON lines from captured stdout."""
    out = capsys.readouterr().out
    lines = []
    for line in out.strip().splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            lines.append(json.loads(line))
    return lines


class TestAuditLoggerStarted:
    def test_emits_started_on_construction(self, capsys: pytest.CaptureFixture[str]) -> None:
        AuditLogger(trace_id="abc123", phase="analysis", model="gpt-4", provider="openai")
        events = _collect_audit_lines(capsys)
        assert len(events) == 1
        e = events[0]
        assert e["event"] == "audit.agent.started"
        assert e["trace_id"] == "abc123"
        assert e["phase"] == "analysis"
        assert e["model"] == "gpt-4"
        assert e["provider"] == "openai"
        assert e["level"] == "audit"
        assert "timestamp" in e


class TestAuditLoggerTextBuffering:
    def test_buffers_text_deltas_emits_on_block_stop(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        al = AuditLogger(trace_id="t1", phase="analysis", model="m", provider="p")
        capsys.readouterr()  # clear started event
        al.process_event(TextDeltaEvent(text="hello "))
        al.process_event(TextDeltaEvent(text="world"))
        assert capsys.readouterr().out == ""  # nothing yet
        al.process_event(ContentBlockStopEvent())
        events = _collect_audit_lines(capsys)
        assert len(events) == 1
        assert events[0]["event"] == "audit.agent.text"
        assert events[0]["text"] == "hello world"

    def test_empty_text_buffer_no_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        al = AuditLogger(trace_id="t1", phase="analysis", model="m", provider="p")
        capsys.readouterr()
        al.process_event(ContentBlockStopEvent())
        assert _collect_audit_lines(capsys) == []


class TestAuditLoggerThinkingBuffering:
    def test_buffers_thinking_emits_on_block_stop(self, capsys: pytest.CaptureFixture[str]) -> None:
        al = AuditLogger(trace_id="t1", phase="analysis", model="m", provider="p")
        capsys.readouterr()
        al.process_event(ThinkingDeltaEvent(thinking="let me think"))
        al.process_event(ContentBlockStopEvent())
        events = _collect_audit_lines(capsys)
        assert len(events) == 1
        assert events[0]["event"] == "audit.agent.thinking"
        assert events[0]["thinking"] == "let me think"


class TestAuditLoggerToolCall:
    def test_emits_tool_call(self, capsys: pytest.CaptureFixture[str]) -> None:
        al = AuditLogger(trace_id="t1", phase="execution", model="m", provider="p")
        capsys.readouterr()
        al.process_event(ToolCallEvent(name="bash", input="ls -la"))
        events = _collect_audit_lines(capsys)
        assert len(events) == 1
        assert events[0]["event"] == "audit.agent.tool.call"
        assert events[0]["tool_name"] == "bash"
        assert events[0]["tool_input"] == "ls -la"


class TestAuditLoggerToolResult:
    def test_emits_tool_result_with_tracked_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        al = AuditLogger(trace_id="t1", phase="execution", model="m", provider="p")
        capsys.readouterr()
        al.process_event(ToolCallEvent(name="bash", input="ls"))
        capsys.readouterr()  # clear tool call event
        al.process_event(ToolResultEvent(output="file1.txt\nfile2.txt"))
        events = _collect_audit_lines(capsys)
        assert len(events) == 1
        assert events[0]["event"] == "audit.agent.tool.result"
        assert events[0]["tool_name"] == "bash"
        assert events[0]["tool_output"] == "file1.txt\nfile2.txt"
        assert events[0]["success"] is True

    def test_tool_result_without_prior_call_uses_unknown(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        al = AuditLogger(trace_id="t1", phase="analysis", model="m", provider="p")
        capsys.readouterr()
        al.process_event(ToolResultEvent(output="result"))
        events = _collect_audit_lines(capsys)
        assert events[0]["tool_name"] == "unknown"


class TestAuditLoggerCompleted:
    def test_emits_completed(self, capsys: pytest.CaptureFixture[str]) -> None:
        al = AuditLogger(trace_id="t1", phase="analysis", model="m", provider="p")
        capsys.readouterr()
        al.complete(success=True, input_tokens=100, output_tokens=50, cost_usd=0.01)
        events = _collect_audit_lines(capsys)
        assert len(events) == 1
        e = events[0]
        assert e["event"] == "audit.agent.completed"
        assert e["success"] is True
        assert e["input_tokens"] == 100
        assert e["output_tokens"] == 50
        assert e["cost_usd"] == 0.01


class TestAuditLoggerCommonFields:
    def test_all_events_carry_required_fields(self, capsys: pytest.CaptureFixture[str]) -> None:
        al = AuditLogger(trace_id="t1", phase="analysis", model="m", provider="p")
        al.process_event(ToolCallEvent(name="bash", input="ls"))
        al.complete(success=True, input_tokens=0, output_tokens=0, cost_usd=0)
        events = _collect_audit_lines(capsys)
        for e in events:
            assert "timestamp" in e
            assert e["level"] == "audit"
            assert e["trace_id"] == "t1"
            assert e["phase"] == "analysis"


class TestAuditLoggerDisabled:
    def test_no_json_when_log_disabled(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When log_enabled=False, no JSON audit lines are emitted but spans still work."""
        al = AuditLogger(
            trace_id="t1", phase="analysis", model="m", provider="p", log_enabled=False
        )
        al.process_event(TextDeltaEvent(text="hello"))
        al.process_event(ContentBlockStopEvent())
        al.process_event(ToolCallEvent(name="bash", input="ls"))
        al.process_event(ToolResultEvent(output="file.txt"))
        al.complete(success=True, input_tokens=100, output_tokens=50, cost_usd=0.01)
        assert capsys.readouterr().out == ""

    def test_spans_created_when_log_disabled(self) -> None:
        """OTEL spans are created even when JSON logging is disabled."""
        al = AuditLogger(
            trace_id="t1", phase="analysis", model="m", provider="p", log_enabled=False
        )
        al.process_event(ToolCallEvent(name="bash", input="ls"))
        assert al._tool_span is not None
        al.process_event(ToolResultEvent(output="done"))
        assert al._tool_span is None
