"""Every Prometheus metric, defined once.

Labels come only from closed sets (alias, provider, status class, a few reasons), never key IDs,
so the number of series stays bounded.
"""

from prometheus_client import Counter, Gauge, Histogram

DURATION_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
TTFT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20)

requests = Counter(
    "gateway_requests", "Requests through the pipeline", ["alias", "provider", "status_class"]
)
duration = Histogram(
    "gateway_request_duration_seconds",
    "Total request duration",
    ["alias", "provider"],
    buckets=DURATION_BUCKETS,
)
ttft = Histogram(
    "gateway_ttft_seconds", "Time to first byte", ["alias", "provider"], buckets=TTFT_BUCKETS
)
in_flight = Gauge("gateway_in_flight_requests", "Requests currently in the pipeline")
tokens = Counter("gateway_tokens", "Tokens", ["alias", "provider", "direction"])
cost = Counter("gateway_cost_usd", "Cost in USD", ["alias", "provider"])
rejected = Counter("gateway_rejected", "Requests rejected before the pipeline", ["reason"])
auth_failures = Counter("gateway_auth_failures", "Failed authentications", ["reason"])
rate_limited = Counter("gateway_rate_limited", "Requests refused by the rate limiter")
budget_exceeded = Counter("gateway_budget_exceeded", "Requests refused by the budget")
cache_requests = Counter("gateway_cache_requests", "Cache lookups", ["result"])
upstream_attempts = Counter("gateway_upstream_attempts", "Provider calls", ["provider", "outcome"])
breaker_state = Gauge("gateway_breaker_state", "0 closed, 1 half-open, 2 open", ["provider"])
usage_write_failures = Counter("gateway_usage_write_failures", "Usage rows that failed to write")
usage_estimated = Counter("gateway_usage_estimated", "Usage rows with estimated tokens")


def status_class(status: int) -> str:
    return "499" if status == 499 else f"{status // 100}xx"
