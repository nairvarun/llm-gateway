from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import ceil

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.domain.errors import GatewayError
from app.domain.models import FailureKind, Principal, StateUnavailable
from app.persistence.models import Attempt, IdempotencyIngress, RequestRecord, UsageEvent
from app.persistence.store import PostgresStore

MAX_ROWS = 10_000


def bounded_window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    # DB and API hosts can differ slightly; include a bounded five-second skew
    # margin for the default window while still reporting its exact UTC bounds.
    end = end or now + timedelta(seconds=5)
    start = start or end - timedelta(days=1)
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or end <= start
        or end - start > timedelta(days=30)
        or end > now + timedelta(minutes=1)
    ):
        raise GatewayError(
            "INVALID_REQUEST", "Use a bounded UTC time window of at most 30 days.", 422
        )
    return start.astimezone(UTC), end.astimezone(UTC)


def percentile(values: list[Decimal], percentile: float) -> str | None:
    if not values:
        return None
    values.sort()
    return str(values[ceil(percentile * len(values)) - 1])


async def summary(
    store: PostgresStore,
    principal: Principal,
    start: datetime,
    end: datetime,
    traffic_kind: str,
) -> dict[str, object]:
    if traffic_kind not in {"application", "evaluation"}:
        raise GatewayError("INVALID_REQUEST", "Unsupported traffic scope.", 422)
    try:
        async with store.sessions() as session:
            requests = (
                await session.scalars(
                    select(RequestRecord)
                    .where(
                        RequestRecord.tenant_id == principal.tenant_id,
                        RequestRecord.application_id == principal.application_id,
                        RequestRecord.traffic_kind == traffic_kind,
                        RequestRecord.created_at >= start,
                        RequestRecord.created_at < end,
                    )
                    .order_by(RequestRecord.created_at)
                    .limit(MAX_ROWS + 1)
                )
            ).all()
            if len(requests) > MAX_ROWS:
                raise GatewayError("INVALID_REQUEST", "Window exceeds the summary row limit.", 422)
            request_ids = [row.id for row in requests]
            attempts = (
                (
                    await session.scalars(
                        select(Attempt).where(Attempt.request_id.in_(request_ids))
                    )
                ).all()
                if request_ids
                else []
            )
            usage = (
                (
                    await session.scalars(
                        select(UsageEvent).where(UsageEvent.request_id.in_(request_ids))
                    )
                ).all()
                if request_ids
                else []
            )
            # Replayed ingress has no new provider request. Historical rows from
            # before application attribution are deliberately not guessed.
            replays = (
                (
                    await session.scalars(
                        select(IdempotencyIngress)
                        .where(
                            IdempotencyIngress.tenant_id == principal.tenant_id,
                            IdempotencyIngress.application_id == principal.application_id,
                            IdempotencyIngress.outcome == "replay",
                            IdempotencyIngress.created_at >= start,
                            IdempotencyIngress.created_at < end,
                        )
                        .limit(MAX_ROWS + 1)
                    )
                ).all()
                if traffic_kind == "application"
                else []
            )
            if len(replays) > MAX_ROWS:
                raise GatewayError("INVALID_REQUEST", "Window exceeds the replay row limit.", 422)
            by_request: dict[object, list[Attempt]] = {}
            for attempt in attempts:
                by_request.setdefault(attempt.request_id, []).append(attempt)
            durations = [
                Decimal(str((row.completed_at - row.created_at).total_seconds() * 1000))
                for row in requests
                if row.completed_at is not None
            ]
            cache_hits = sum(
                bool((row.routing_evidence or {}).get("cache_hit")) for row in requests
            )
            successful = sum(row.status == "completed" for row in requests)
            fallback_requests = sum(
                len({(item.provider, item.model) for item in by_request.get(row.id, [])}) > 1
                for row in requests
            )
            return {
                "starts_at": start,
                "ends_at": end,
                "traffic_kind": traffic_kind,
                "gateway_requests": len(requests),
                "completed_requests": successful,
                "success_rate": str(Decimal(successful) / len(requests)) if requests else None,
                "p50_latency_ms": percentile(durations.copy(), 0.50),
                "p95_latency_ms": percentile(durations.copy(), 0.95),
                "provider_attempts": len(attempts),
                "provider_errors": sum(
                    item.error_class in {kind.value for kind in FailureKind} for item in attempts
                ),
                "retries": sum(item.number > 1 for item in attempts),
                "fallback_requests": fallback_requests,
                "fallback_rate": (
                    str(Decimal(fallback_requests) / len(requests)) if requests else None
                ),
                "cache_hits": cache_hits,
                "cache_hit_rate": str(Decimal(cache_hits) / len(requests)) if requests else None,
                "idempotency_replays": len(replays),
                "input_tokens": sum(item.input_tokens or 0 for item in usage),
                "output_tokens": sum(item.output_tokens or 0 for item in usage),
                "estimated_fresh_cost_usd": str(
                    sum((item.estimated_cost_usd for item in usage), Decimal(0))
                ),
                "unknown_usage_attempts": sum(item.usage_status == "unknown" for item in usage),
            }
    except (SQLAlchemyError, OSError, TimeoutError) as error:
        raise StateUnavailable() from error
