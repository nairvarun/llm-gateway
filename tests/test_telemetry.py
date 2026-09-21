import logging
from collections.abc import Sequence
from uuid import UUID

import httpx
import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from app.config import Settings
from app.domain.control import LocalControl
from app.main import create_app
from app.observability.telemetry import Telemetry
from app.persistence.bootstrap import bootstrap_local
from app.persistence.store import PostgresStore
from tests.fakes import MemoryStore


class FailedExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


async def test_exporter_failure_is_observable_and_does_not_break_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    memory = MemoryStore()
    settings = Settings(trace_sample_rate=1)
    telemetry = Telemetry(settings, exporter=FailedExporter())
    app = create_app(settings, store=memory, control=LocalControl(), telemetry=telemetry)
    with caplog.at_level(logging.INFO, logger="gateway.request"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/generate",
                json={"input": "synthetic-secret-sentinel"},
                headers={"X-API-Key": memory.key},
            )
        assert response.status_code == 200
        assert telemetry.provider.force_flush()
        assert telemetry.health == "degraded"
        encoded = telemetry.render()[0].decode()
        assert "gateway_exporter_failures_total 1.0" in encoded
        assert 'gateway_requests_total{route="generate",status_class="2xx"} 1.0' in encoded
        assert "synthetic-secret-sentinel" not in encoded
        assert str(response.json()["request_id"]) not in encoded
        assert "synthetic-secret-sentinel" not in caplog.text
        assert '"route":"generate"' in caplog.text
    telemetry.close()


async def test_sampled_out_trace_keeps_durable_request_evidence(postgres: PostgresStore) -> None:
    key, principal = await bootstrap_local(postgres, role="operator")
    telemetry = Telemetry(Settings(trace_sample_rate=0))
    app = create_app(
        Settings(trace_sample_rate=0), store=postgres, control=LocalControl(), telemetry=telemetry
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/generate", json={"input": "synthetic"}, headers={"X-API-Key": key}
        )
        assert response.status_code == 200
        evidence = await postgres.evidence(principal, UUID(response.json()["request_id"]))
        assert evidence is not None
        metrics = await client.get("/metrics", headers={"X-API-Key": key})
        assert metrics.status_code == 200
        assert "gateway_attempts_total" in metrics.text
    telemetry.close()
