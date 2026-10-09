# Phase 5: observability

**In two sentences.** The outermost stage records RED metrics, writes one JSON log line per
request and (optionally) opens a trace span, with a child span per provider attempt. Labels come
only from closed sets, so the number of series stays bounded.

## What I learned

- **TTFT is the latency that matters for streams.** Total duration mostly measures how long the
  answer is; time to first byte measures the gateway and the provider.
- **Cardinality is a design decision.** Key IDs would make a series per customer; they live in
  the usage table instead. Provider model IDs were dropped as a label too (spec, Phase 5 label rules),
  because alias plus provider answers the operational questions.
- **Count what you reject before the pipeline.** Malformed bodies, unknown models and requests
  during draining never reach `observe`, so they get their own counter
  (`gateway_rejected_total{reason}`).
- **Client disconnects are not errors.** They get their own status class (499) so they do not
  trip the error-rate alert.
- **Egress policy has limits.** Vanilla NetworkPolicy matches IPs, not hostnames, so the policy
  allows DNS and port 443 only. Hostname rules would need an FQDN-aware CNI.

## Artifacts

`deploy/observability/grafana-dashboard.json` (12 panels) and `deploy/observability/alerts.yaml`
(5xx ratio above 5% for 10 minutes; p95 TTFT above 5 s for 15 minutes).
