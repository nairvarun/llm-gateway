# Milestone 5 benchmark — synthetic local evidence

Measured 21 September 2026 UTC. This is a seven-case, two-repetition, sequential
offline contract exercise on a Darwin host with loopback PostgreSQL and Redis,
Python 3.12.14, `mock-text-v1@v2`, `mock-policy@v2`, zero-USD synthetic pricing,
temperature zero, 512 maximum output tokens, one attempt, and a 30-second
deadline. The [raw sanitized evidence](benchmark-evidence-v1.json) records the
code/dataset hashes, every case outcome, elapsed time, scorer result, and full
configuration. No prompt, response, credential, or provider network call is
stored in that evidence. Rerun with local dependencies:

```sh
uv run python -m deploy.benchmark --output docs/benchmark-evidence-v1.json
uv run pytest -q tests/test_benchmark.py
```

The script creates and drops only its own random PostgreSQL schema and isolates
Redis keys by random prefix. It deliberately bypasses cache in the direct mock
and gateway-bypass conditions, then opts synthetic content into exact caching
for the repeated gateway condition. p95 uses nearest rank over all 14 requests,
including failures; gateway overhead is ingress-to-terminal wall time minus
the measured mock invocation wall time. No warm-up, parallelism, confidence
interval, or live-model calibration is claimed. Timing numbers vary by host/load.

| Condition | Successful / all | Mock invocations | Exact hits | p95 end-to-end | p95 gateway overhead |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct fixed mock | 8 / 14 | 14 | 0 | 0.089 ms | 0 ms (not a gateway) |
| Gateway, cache bypass | 8 / 14 | 14 | 0 | 53.614 ms | 53.597 ms |
| Gateway, repeated exact cache | 8 / 14 | 10 | 4 / 14 | 45.302 ms | 45.288 ms |

The repeated-cache workload recorded four hits (28.6%) and improved aggregate
p95 latency against bypass in this run; this single small sample is sensitive to
host load and cannot establish a general speedup. All conditions scored
extraction validity 4/10 (40%), field accuracy 6/8 (75%), classification
macro-F1 0, and two generation cases awaiting human review. Four failures are
deliberately invalid/ambiguous extraction inputs; two long cases truncate.
Failures remain in denominators. Estimated fresh cost is zero
in all three conditions; no cost-reduction percentage exists for a zero-cost
baseline, and no provider invoice was measured.

| Handoff aspiration | This measured workload | Interpretation |
| --- | --- | --- |
| >=99% extraction validity | 40% | Not met; deliberately adverse fixture and mock limitation |
| <100 ms p95 gateway overhead | 53.597 ms bypass; 45.288 ms cached | Met only on this small local sequential mock run; not a staging SLO |
| >=20% lower inference cost at equal quality | Both mock paths USD 0 | Not measurable with free synthetic pricing |
| >=25% exact hits on declared repeats | 4/14 = 28.6% | Met only on this repeated synthetic mix |
| >=99% recovery on defined transient faults | Not in this workload | Covered separately by fault tests, not benchmarked here |

The versioned evaluation profile in
[`datasets/profiles/synthetic-contract-v1.json`](../datasets/profiles/synthetic-contract-v1.json)
also requires a baseline, stronger task-specific quality, human generation
review, and cost/latency evidence. The local demo intentionally has no approved
baseline and reports `blocked`; this benchmark neither approves a baseline nor
promotes a policy. Live-provider quality, invoice cost, throughput, staging
latency, and uncertainty bounds remain unmeasured.
