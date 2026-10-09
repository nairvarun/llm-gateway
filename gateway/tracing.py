"""Optional OpenTelemetry tracing: one span per request, a child span per provider attempt.

Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set and the `tracing` extra is installed.
Every function here is a no-op when tracing is off.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("gateway.tracing")
_tracer: Any = None


def setup(endpoint: str | None) -> None:
    global _tracer
    if not endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTEL endpoint set but opentelemetry is not installed; tracing is off")
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "llm-gateway"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("gateway")


def start_request_span(request_id: str, alias: str) -> Any:
    if _tracer is None:
        return None
    return _tracer.start_span(
        "chat.completions", attributes={"request_id": request_id, "alias": alias}
    )


def start_attempt_span(parent: Any, provider: str, model: str, attempt: int) -> Any:
    if _tracer is None or parent is None:
        return None
    from opentelemetry import trace

    ctx = trace.set_span_in_context(parent)
    return _tracer.start_span(
        "provider.attempt",
        context=ctx,
        attributes={"provider": provider, "model": model, "attempt": attempt},
    )


def end(span: Any, **attributes: Any) -> None:
    if span is None:
        return
    for k, v in attributes.items():
        if v is not None:
            span.set_attribute(k, v)
    span.end()
