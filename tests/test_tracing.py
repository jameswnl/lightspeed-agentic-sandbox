"""Tests for OTEL tracing initialization and traceparent parsing."""

from __future__ import annotations

import re

import pytest
from opentelemetry import trace

from lightspeed_agentic.tracing import get_tracer, init_tracer, parse_traceparent, shutdown_tracer

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class TestParseTraceparent:
    def test_valid_traceparent(self) -> None:
        header = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        trace_id, ctx = parse_traceparent(header)
        assert trace_id == "0af7651916cd43dd8448eb211c80319c"
        assert ctx is not None

    def test_none_header_generates_trace_id(self) -> None:
        trace_id, ctx = parse_traceparent(None)
        assert _TRACE_ID_RE.match(trace_id)
        assert ctx is not None

    def test_empty_header_generates_trace_id(self) -> None:
        trace_id, ctx = parse_traceparent("")
        assert _TRACE_ID_RE.match(trace_id)
        assert ctx is not None

    def test_malformed_header_generates_trace_id(self) -> None:
        trace_id, ctx = parse_traceparent("not-a-traceparent")
        assert _TRACE_ID_RE.match(trace_id)
        assert ctx is not None

    def test_wrong_field_count_generates_trace_id(self) -> None:
        trace_id, ctx = parse_traceparent("00-abc-01")
        assert _TRACE_ID_RE.match(trace_id)
        assert ctx is not None

    def test_all_zero_trace_id_generates_new(self) -> None:
        header = "00-00000000000000000000000000000000-b7ad6b7169203331-01"
        trace_id, ctx = parse_traceparent(header)
        assert trace_id != "00000000000000000000000000000000"
        assert _TRACE_ID_RE.match(trace_id)
        assert ctx is not None

    def test_short_parent_id_generates_new(self) -> None:
        header = "00-0af7651916cd43dd8448eb211c80319c-b7ad-01"
        trace_id, ctx = parse_traceparent(header)
        assert trace_id != "0af7651916cd43dd8448eb211c80319c"
        assert _TRACE_ID_RE.match(trace_id)
        assert ctx is not None

    def test_generated_ids_are_unique(self) -> None:
        id1, _ = parse_traceparent(None)
        id2, _ = parse_traceparent(None)
        assert id1 != id2


class TestInitTracer:
    def test_init_without_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        init_tracer()
        tracer = get_tracer()
        assert tracer is not None

    def test_init_with_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        init_tracer()
        tracer = get_tracer()
        assert tracer is not None

    def test_get_tracer_returns_named_tracer(self) -> None:
        tracer = get_tracer()
        assert isinstance(tracer, trace.Tracer)

    def test_shutdown_tracer_flushes_without_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        init_tracer()
        shutdown_tracer()
