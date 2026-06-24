"""OTEL tracing — tracer initialization and traceparent parsing."""

from __future__ import annotations

import os
import secrets

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

_SERVICE_NAME = "lightspeed-agentic-sandbox"
_TRACER_NAME = "lightspeed_agentic"
_tracer_provider: TracerProvider | None = None


def init_tracer() -> None:
    global _tracer_provider
    resource = Resource.create({"service.name": _SERVICE_NAME})
    _tracer_provider = TracerProvider(resource=resource)
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=not endpoint.startswith("https"))
        _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_tracer_provider)


def shutdown_tracer() -> None:
    if _tracer_provider:
        _tracer_provider.shutdown()


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


def parse_traceparent(header: str | None) -> tuple[str, Context | None]:
    if header:
        parts = header.split("-")
        if len(parts) >= 4:
            trace_id_hex = parts[1]
            parent_id_hex = parts[2]
            flags_hex = parts[3]
            if (
                len(trace_id_hex) == 32
                and trace_id_hex != "0" * 32
                and len(parent_id_hex) == 16
                and parent_id_hex != "0" * 16
            ):
                try:
                    trace_id = int(trace_id_hex, 16)
                    parent_id = int(parent_id_hex, 16)
                    flags = int(flags_hex, 16)
                except ValueError:
                    return _generate_trace_id()
                span_ctx = SpanContext(
                    trace_id=trace_id,
                    span_id=parent_id,
                    is_remote=True,
                    trace_flags=TraceFlags(flags),
                )
                ctx = trace.set_span_in_context(NonRecordingSpan(span_ctx))
                return trace_id_hex, ctx
    return _generate_trace_id()


def _generate_trace_id() -> tuple[str, Context]:
    trace_id_hex = secrets.token_hex(16)
    span_id_hex = secrets.token_hex(8)
    span_ctx = SpanContext(
        trace_id=int(trace_id_hex, 16),
        span_id=int(span_id_hex, 16),
        is_remote=False,
        trace_flags=TraceFlags(1),
    )
    ctx = trace.set_span_in_context(NonRecordingSpan(span_ctx))
    return trace_id_hex, ctx
