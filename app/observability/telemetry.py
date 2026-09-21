"""Best-effort, bounded-cardinality metrics and sanitized traces/logs."""

import json
import logging
from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

from app.config import Settings
from app.domain.models import TokenUsage

ROUTES = {
    "/v1/generate": "generate",
    "/v1/extract": "extract",
    "/v1/spend": "spend",
    "/v1/evaluations/runs": "evaluation_run",
    "/v1/metrics/summary": "summary",
    "/health/live": "live",
    "/health/ready": "ready",
    "/metrics": "metrics",
    "/docs": "docs",
    "/openapi.json": "openapi",
}


def route_label(path: str) -> str:
    if path.startswith("/v1/evaluations/runs/"):
        return "evaluation_run"
    return ROUTES.get(path, "other")


class ObservedExporter(SpanExporter):
    def __init__(self, delegate: SpanExporter, failures: Counter) -> None:
        self.delegate = delegate
        self.failures = failures
        self.degraded = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self.delegate.export(spans)
            if result != SpanExportResult.SUCCESS:
                self.failures.inc()
                self.degraded = True
            return result
        except Exception:
            self.failures.inc()
            self.degraded = True
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self.delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.delegate.force_flush(timeout_millis)


class Telemetry:
    def __init__(self, settings: Settings, *, exporter: SpanExporter | None = None) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.request_count = Counter(
            "gateway_requests_total",
            "HTTP request outcomes by fixed route and status class",
            ["route", "status_class"],
            registry=self.registry,
        )
        self.request_duration = Histogram(
            "gateway_request_duration_seconds",
            "HTTP ingress-to-response wall time",
            ["route"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 120),
            registry=self.registry,
        )
        self.attempt_count = Counter(
            "gateway_attempts_total",
            "Provider attempts by sanitized status and usage availability",
            ["status", "usage_status"],
            registry=self.registry,
        )
        self.token_count = Counter(
            "gateway_tokens_total",
            "Fresh provider token estimates",
            ["direction"],
            registry=self.registry,
        )
        self.estimated_cost = Counter(
            "gateway_estimated_cost_usd_total",
            "Fresh attempt estimated liability",
            registry=self.registry,
        )
        self.cache_count = Counter(
            "gateway_cache_total", "Exact cache outcome", ["outcome"], registry=self.registry
        )
        self.retries = Counter(
            "gateway_retries_total", "Additional provider attempts", registry=self.registry
        )
        self.fallbacks = Counter(
            "gateway_fallback_attempts_total",
            "Attempts on alternate models",
            registry=self.registry,
        )
        self.timeouts = Counter(
            "gateway_timeouts_total", "Provider/deadline timeouts", registry=self.registry
        )
        self.circuits = Counter(
            "gateway_circuit_rejections_total", "Open-circuit admissions", registry=self.registry
        )
        self.validation_failures = Counter(
            "gateway_validation_failures_total",
            "Rejected extraction output",
            registry=self.registry,
        )
        self.export_failures = Counter(
            "gateway_exporter_failures_total",
            "Best-effort trace export failures",
            registry=self.registry,
        )
        self.instrumentation_failures = Counter(
            "gateway_telemetry_record_failures_total",
            "Best-effort instrumentation failures",
            registry=self.registry,
        )
        self.provider = TracerProvider(
            resource=Resource.create({"service.name": "llm-reliability-gateway"}),
            sampler=TraceIdRatioBased(settings.trace_sample_rate),
        )
        configured = settings.otlp_traces_endpoint
        if exporter is None and configured is not None:
            exporter = OTLPSpanExporter(
                endpoint=configured.get_secret_value().rstrip("/") + "/v1/traces", timeout=1
            )
        self.observed_exporter = (
            ObservedExporter(exporter, self.export_failures) if exporter is not None else None
        )
        if self.observed_exporter is not None:
            self.provider.add_span_processor(
                BatchSpanProcessor(
                    self.observed_exporter,
                    max_queue_size=512,
                    max_export_batch_size=32,
                    schedule_delay_millis=1000,
                    export_timeout_millis=1000,
                )
            )
        self.tracer = self.provider.get_tracer("gateway")

    @property
    def health(self) -> str:
        if self.observed_exporter is None:
            return "not_configured"
        return "degraded" if self.observed_exporter.degraded else "healthy"

    def observe_http(self, path: str, status: int, elapsed: float, request_id: UUID) -> None:
        route = route_label(path)
        status_class = f"{max(1, min(status // 100, 5))}xx"
        try:
            self.request_count.labels(route, status_class).inc()
            self.request_duration.labels(route).observe(max(0, elapsed))
            logging.getLogger("gateway.request").info(
                json.dumps(
                    {
                        "event": "request_finished",
                        "request_id": str(request_id),
                        "route": route,
                        "status": status,
                        "elapsed_ms": round(max(0, elapsed) * 1000, 3),
                    },
                    separators=(",", ":"),
                )
            )
        except Exception:
            self.instrumentation_failures.inc()

    def observe_attempt(
        self,
        status: str,
        usage: TokenUsage,
        cost: Decimal,
        attempt_number: int = 1,
        fallback: bool = False,
        error_class: str | None = None,
    ) -> None:
        try:
            allowed_status = (
                status
                if status in {"completed", "failed", "uncertain", "not_dispatched"}
                else "other"
            )
            allowed_usage = (
                usage.status
                if usage.status in {"observed", "synthetic", "unknown", "partial_unknown"}
                else "other"
            )
            self.attempt_count.labels(allowed_status, allowed_usage).inc()
            self.token_count.labels("input").inc(usage.input_tokens or 0)
            self.token_count.labels("output").inc(usage.output_tokens or 0)
            self.estimated_cost.inc(float(max(Decimal(0), cost)))
            if attempt_number > 1:
                self.retries.inc()
            if fallback:
                self.fallbacks.inc()
            if error_class in {"timeout", "deadline"}:
                self.timeouts.inc()
            if error_class == "circuit_open":
                self.circuits.inc()
            if error_class in {"output_validation", "output_refusal", "output_truncation"}:
                self.validation_failures.inc()
        except Exception:
            self.instrumentation_failures.inc()

    def observe_cache(self, outcome: str) -> None:
        try:
            allowed = (
                outcome
                if outcome in {"bypass", "ineligible", "miss", "hit", "degraded"}
                else "other"
            )
            self.cache_count.labels(allowed).inc()
        except Exception:
            self.instrumentation_failures.inc()

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST

    def close(self) -> None:
        self.provider.shutdown()
