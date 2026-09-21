"""One-shot synthetic offline benchmark; never makes a paid provider call."""

import argparse
import asyncio
import base64
import json
import os
import platform
from datetime import UTC, datetime
from decimal import Decimal
from math import ceil
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import uuid4

import httpx
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.schema_validation import validate_output
from app.cache.redis_cache import RedisCache
from app.config import Settings
from app.domain.errors import GatewayError
from app.domain.models import FinishReason, ProviderInput, ProviderResult
from app.evaluation.datasets import DatasetCase, load_dataset
from app.evaluation.revision import code_revision
from app.evaluation.scoring import aggregate, score_case
from app.main import create_app
from app.persistence.bootstrap import bootstrap_local
from app.persistence.database import database_engine
from app.persistence.store import PostgresStore
from app.providers.mock import MockProvider

REPETITIONS = 2
MAX_OUTPUT_TOKENS = 512
SECRET = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


class TimedMock(MockProvider):
    def __init__(self) -> None:
        super().__init__()
        self.wall_seconds = 0.0

    async def invoke(self, request: ProviderInput) -> ProviderResult:
        started = monotonic()
        try:
            return await super().invoke(request)
        finally:
            self.wall_seconds += monotonic() - started


def percentile(values: list[float], quantile: float) -> float:
    return round(sorted(values)[ceil(len(values) * quantile) - 1], 3) if values else 0.0


def case_payload(case: DatasetCase, *, cache: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "input": case.resolved_input,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "cache_mode": "read_write" if cache else "bypass",
        "cache_classification": "approved_non_sensitive" if cache else "sensitive",
    }
    if case.task == "extraction":
        payload["json_schema"] = case.schema_
    else:
        payload["task_type"] = case.task
    return payload


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    scores = [cast(dict[str, Any], row["score"]) for row in rows]
    report = aggregate(scores)
    return {
        "requests": len(rows),
        "successes": sum(row["status"] == "completed" for row in rows),
        "cache_hits": sum(row["cache_status"] == "hit" for row in rows),
        "provider_invocations": sum(int(str(row["provider_invocations"])) for row in rows),
        "p95_end_to_end_ms": percentile([float(str(row["wall_ms"])) for row in rows], 0.95),
        "p95_gateway_overhead_ms": percentile(
            [float(str(row["overhead_ms"])) for row in rows], 0.95
        ),
        "estimated_fresh_cost_usd": str(
            sum((Decimal(str(row["estimated_cost_usd"])) for row in rows), Decimal(0))
        ),
        "scores": report,
    }


async def baseline(cases: list[DatasetCase]) -> list[dict[str, object]]:
    adapter = TimedMock()
    records = []
    for repetition in range(REPETITIONS):
        for case in cases:
            started = monotonic()
            output: object | None = None
            error_code: str | None = None
            try:
                result = await adapter.invoke(
                    ProviderInput(
                        case.resolved_input,
                        case.task,
                        case.schema_,
                        0,
                        MAX_OUTPUT_TOKENS,
                    )
                )
                if case.task == "extraction":
                    if result.finish_reason != FinishReason.STOP:
                        raise GatewayError("OUTPUT_VALIDATION_FAILED", "Synthetic truncation.", 502)
                    assert case.schema_ is not None
                    output = validate_output(result.output, case.schema_)
                else:
                    output = result.output
            except GatewayError as error:
                error_code = error.code
            elapsed = (monotonic() - started) * 1000
            records.append(
                {
                    "case_id": case.id,
                    "repetition": repetition + 1,
                    "task": case.task,
                    "difficulty": case.difficulty,
                    "status": "failed" if error_code else "completed",
                    "error_code": error_code,
                    "cache_status": "bypass",
                    "provider_invocations": 1,
                    "estimated_cost_usd": "0",
                    "wall_ms": round(elapsed, 3),
                    "overhead_ms": 0.0,
                    "score": score_case(case, output, error_code),
                }
            )
    return records


async def gateway_condition(
    store: PostgresStore,
    key: str,
    cases: list[DatasetCase],
    *,
    cache: RedisCache | None,
) -> list[dict[str, object]]:
    provider = TimedMock()
    settings = Settings(
        redis_url="redis://127.0.0.1:56379",
        cache_encryption_key=SECRET if cache is not None else None,
    )
    app = create_app(settings, store=store, provider=provider, cache=cache)
    rows = []
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://benchmark",
            headers={"X-API-Key": key},
        ) as client:
            for repetition in range(REPETITIONS):
                for case in cases:
                    prior_invocations = provider.invocations
                    prior_provider_seconds = provider.wall_seconds
                    started = monotonic()
                    response = await client.post(
                        "/v1/extract" if case.task == "extraction" else "/v1/generate",
                        json=case_payload(case, cache=cache is not None),
                    )
                    elapsed = (monotonic() - started) * 1000
                    if response.status_code not in {200, 502}:
                        raise RuntimeError("Unexpected benchmark gateway response status")
                    body = response.json()
                    error_code = body.get("error", {}).get("code")
                    overhead = max(
                        0.0, elapsed - (provider.wall_seconds - prior_provider_seconds) * 1000
                    )
                    rows.append(
                        {
                            "case_id": case.id,
                            "repetition": repetition + 1,
                            "task": case.task,
                            "difficulty": case.difficulty,
                            "status": "completed" if response.status_code == 200 else "failed",
                            "error_code": error_code,
                            "request_id": body["request_id"],
                            "cache_status": body.get("cache_status", "bypass"),
                            "provider_invocations": provider.invocations - prior_invocations,
                            "estimated_cost_usd": body.get("estimated_cost_usd", "0"),
                            "wall_ms": round(elapsed, 3),
                            "overhead_ms": round(overhead, 3),
                            "score": score_case(case, body.get("output"), error_code),
                        }
                    )
    return rows


async def run() -> dict[str, object]:
    dataset = load_dataset("synthetic-gateway", "v1")
    cases = dataset.cases
    settings = Settings()
    url = settings.database_url.get_secret_value()
    schema = "bench_" + uuid4().hex
    cache_prefix = "bench-cache:" + uuid4().hex
    admin = database_engine(url)
    redis = Redis.from_url("redis://127.0.0.1:56379", socket_timeout=1)
    await redis.ping()
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = database_engine(url, schema)
    try:
        config = Config("alembic.ini")
        config.set_main_option("database_url", url.replace("%", "%%"))
        config.set_main_option("database_schema", schema)
        await asyncio.to_thread(command.upgrade, config, "head")
        store = PostgresStore(engine)
        key, _ = await bootstrap_local(store)
        baseline_rows = await baseline(cases)
        bypass_rows = await gateway_condition(store, key, cases, cache=None)
        cached_rows = await gateway_condition(
            store, key, cases, cache=RedisCache(redis, prefix=cache_prefix)
        )
        evidence = {
            "measured_at": datetime.now(UTC).isoformat(),
            "environment": f"{platform.system()} host with local PostgreSQL/Redis loopback",
            "runtime": platform.python_version(),
            "code_revision_sha256": code_revision(),
            "dataset": f"{dataset.name}@{dataset.version}",
            "dataset_sha256": dataset.content_sha256,
            "case_count": len(cases),
            "difficulty_mix": sorted({case.difficulty for case in cases}),
            "repetitions": REPETITIONS,
            "concurrency": 1,
            "warmup": 0,
            "provider": "synthetic mock-text-v1@v2 only",
            "policy": "mock-policy@v2",
            "pricing": "mock-text-v1@v2 zero USD",
            "sampling": {"temperature": 0, "max_output_tokens": MAX_OUTPUT_TOKENS},
            "deadline_ms": 30000,
            "attempt_limit": 1,
            "cache_ttl_seconds": 3600,
            "conditions": {
                "fixed_mock_direct": {"summary": summarize(baseline_rows), "cases": baseline_rows},
                "gateway_bypass": {"summary": summarize(bypass_rows), "cases": bypass_rows},
                "gateway_exact_repeated": {"summary": summarize(cached_rows), "cases": cached_rows},
            },
            "limitations": [
                "Offline mock does not measure live-model quality, latency, or invoice cost.",
                "Sequential local workload is not production throughput or staging SLO evidence.",
                "Failure HTTP errors omit attempt cost; pinned mock pricing is zero USD.",
            ],
        }
        return evidence
    finally:
        await engine.dispose()
        keys = [key async for key in redis.scan_iter(f"{cache_prefix}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write sanitized JSON evidence to this file")
    args = parser.parse_args()
    if os.environ.get("GATEWAY_PROVIDER", "mock") != "mock":
        raise ValueError("Benchmark can only use offline mock provider")
    evidence = json.dumps(asyncio.run(run()), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(evidence, end="")
    else:
        args.output.write_text(evidence)
        print(f"Sanitized benchmark evidence: {args.output}")


if __name__ == "__main__":
    main()
