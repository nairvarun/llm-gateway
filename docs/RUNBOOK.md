# LLM Gateway test runbook

Written 2026-10-12 against the K1 deploy on EKS (branch v2). Untested end to end; fix what drifts.

Run every section top to bottom from `~/dev/lmgate` on your Mac against the live gateway at `https://lmgate.nairvarun.com`. Each step says what you should see. Real calls cost real (small) money; the whole runbook is well under a dollar.

## Setup

One terminal, kept open for the whole run. Needs `jq` (`brew install jq` if missing).

```bash
cd ~/dev/lmgate
export GW=https://lmgate.nairvarun.com
export ADMIN=$(grep ^ADMIN_TOKEN= deploy/k8s/base/secrets.env | cut -d= -f2)

# /admin and /metrics are not public: reach them through a port-forward
kubectl -n llm-gateway port-forward deploy/gateway 8080 >/dev/null &
export LOCAL=http://localhost:8080

# helpers
mk()   { curl -s -X POST $LOCAL/admin/keys -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' -d "$1"; }
chat() { curl -s $GW/v1/chat/completions -H "Authorization: Bearer $1" -H 'content-type: application/json' -d "$2"; }
metric() { curl -s $LOCAL/metrics | grep -E "^$1"; }

# main test key
R=$(mk '{"name":"runbook"}'); export KEY=$(echo $R | jq -r .key) KEY_ID=$(echo $R | jq -r .id)
echo $KEY_ID
```

Expect: a `key_…` id. `$KEY` starts with `gw-`.

## 1. Proxy basics

1. **List models.** `curl -s $GW/v1/models -H "Authorization: Bearer $KEY" | jq '.data[].id'`. Expect `"fast"` and `"smart"`.
2. **Non-streaming, both providers.**

   ```bash
   chat $KEY '{"model":"fast","messages":[{"role":"user","content":"Say hi in 3 words"}]}' | jq '{model, text: .choices[0].message.content, usage}'
   chat $KEY '{"model":"smart","messages":[{"role":"user","content":"Say hi in 3 words"}]}' | jq '{model, text: .choices[0].message.content, usage}'
   ```

   Expect: a reply and a `usage` block from each. `smart` goes to Anthropic but comes back in OpenAI format.
3. **Streaming.**

   ```bash
   curl -sN $GW/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
     -d '{"model":"smart","stream":true,"messages":[{"role":"user","content":"Count from 1 to 30 slowly"}]}'
   ```

   Expect: `data: {...}` lines arriving one by one, ending with `data: [DONE]`. No `usage` chunk, because you didn't ask for one. Add `"stream_options":{"include_usage":true}` and a final chunk with usage appears.
4. **Request ids.** `curl -si $GW/v1/models -H "Authorization: Bearer $KEY" -H 'x-request-id: runbook-1' | grep -i x-request-id`. Expect `runbook-1` echoed back. Without the header you get a generated id.
5. **Auth errors.**
   - No key: `curl -s -o /dev/null -w '%{http_code}\n' $GW/v1/models`. Expect `401`.
   - Wrong key: same with `-H 'Authorization: Bearer gw-nope'`. Expect `401` and an OpenAI-style `{"error":{...}}` body.
6. **Tools need a capable target.** Send a request with a `tools` array to `smart` (Anthropic only, no tool translation). Expect `400`. The same request to `fast` goes to OpenAI and works.

## 2. Virtual keys and admin auth

1. **Model restriction.**

   ```bash
   R=$(mk '{"name":"fast-only","allowed_models":["fast"]}'); FAST=$(echo $R | jq -r .key); FAST_ID=$(echo $R | jq -r .id)
   curl -s $GW/v1/models -H "Authorization: Bearer $FAST" | jq '.data[].id'
   chat $FAST '{"model":"smart","messages":[{"role":"user","content":"hi"}]}' | jq .error
   ```

   Expect: only `"fast"` listed, and a `403` error for `smart`.
2. **Unknown alias at creation.** `mk '{"name":"bad","allowed_models":["gpt-9"]}'`. Expect a `400` naming the unknown model.
3. **Admin needs the admin token.**
   - No token: `curl -s -o /dev/null -w '%{http_code}\n' -X POST $LOCAL/admin/keys`. Expect `401`.
   - A virtual key: same with `-H "Authorization: Bearer $KEY"`. Expect `403`.
4. **Admin is not public.** `curl -s -o /dev/null -w '%{http_code}\n' $GW/admin/keys`. Expect `404` from the load balancer, since only `/v1` is routed.
5. **Revoke.** `curl -s -o /dev/null -w '%{http_code}\n' -X DELETE $LOCAL/admin/keys/$FAST_ID -H "Authorization: Bearer $ADMIN"`. Expect `204`. Then `$FAST` on `/v1/models` gets `401`, and revoking it again gets `404`.

## 3. Usage and cost tracking

1. **Per-key summary.** `curl -s $LOCAL/admin/keys/$KEY_ID/usage -H "Authorization: Bearer $ADMIN" | jq`. Expect a row per alias (`fast`, `smart`) with `requests`, `input_tokens`, `output_tokens` and a non-zero `cost_usd`.
2. **Raw usage rows.** Define a helper that reads the SQLite file in the pod:

   ```bash
   rows() { kubectl -n llm-gateway exec deploy/gateway -- python -c "import sqlite3; c=sqlite3.connect('file:/data/gateway.db?mode=ro',uri=True); [print(r) for r in c.execute('SELECT ts,model_alias,provider,model,attempts,input_tokens,output_tokens,usage_estimated,round(cost_usd,6),status,cache_hit FROM usage ORDER BY id DESC LIMIT ${1:-5}')]"; }
   rows 10
   ```

   Expect one row per authenticated request so far (the 401s have no key, so they leave no row). Real token counts, `usage_estimated` 0, `attempts` 1.
3. **Cut-short stream is estimated.** Start a long stream and kill it after 2 seconds:

   ```bash
   curl --max-time 2 -sN $GW/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
     -d '{"model":"smart","stream":true,"messages":[{"role":"user","content":"Write 500 words about rivers"}]}' >/dev/null; sleep 2; rows 1
   ```

   Expect: status `499` (client went away), `usage_estimated` 1, output tokens roughly the characters received divided by 4.

## 4. Rate limits and budgets

```bash
HI='{"model":"fast","messages":[{"role":"user","content":"hi"}]}'
code() { curl -s -o /dev/null -w '%{http_code}\n' -D - $GW/v1/chat/completions -H "Authorization: Bearer $1" -H 'content-type: application/json' -d "$HI" | grep -iE '^HTTP|retry-after|^[0-9]{3}$'; }
```

1. **Requests per minute.** The bucket starts full at `rpm_limit` and refills at rpm/60 per second.

   ```bash
   SLOW=$(mk '{"name":"rpm2","rpm_limit":2}' | jq -r .key)
   for i in 1 2 3; do code $SLOW; done
   ```

   Expect `200`, `200`, then `429` with a `retry-after` header in whole seconds. After waiting that long, the next call is `200` again.
2. **Monthly budget.** A tiny cap is used up by the first call:

   ```bash
   CHEAP=$(mk '{"name":"budget","monthly_budget_usd":0.000001}' | jq -r .key)
   code $CHEAP; code $CHEAP
   ```

   Expect `200`, then `402` with "monthly budget … is used up". The cap is soft by design: parallel requests can each slip past the check before any cost is recorded.
3. **Metrics moved.** `metric 'gateway_(rate_limited|budget_exceeded)'`. Expect both counters at 1 or more.

## 5. Resilience (fault injection)

These steps break the config on purpose, roll the pod, test, then restore. A rollout restarts the pod, which also resets the in-memory rate limiter, cache and breakers. Re-run the port-forward after each rollout.

```bash
roll() { kubectl apply -k deploy/k8s/overlays/eks >/dev/null && kubectl -n llm-gateway rollout status deploy/gateway; kubectl -n llm-gateway port-forward deploy/gateway 8080 >/dev/null & sleep 3; }
```

**A. Fallback and circuit breaker.** Point OpenAI at a dead address:

```bash
sed -i '' 's#base_url: https://api.openai.com/v1#base_url: https://127.0.0.1:9/v1#' deploy/k8s/base/config.yaml && roll
```

1. **Fallback.** `chat $KEY "$HI" | jq .model; rows 1`. Expect a normal reply. The row shows provider `anthropic`, model `claude-haiku-5-5` and `attempts` of 2 or more (OpenAI failed, then Haiku answered).
2. **Breaker opens.** `for i in $(seq 6); do chat $KEY "$HI" >/dev/null; done; metric gateway_breaker_state`. Expect `provider="openai"` at `2` (open) and anthropic at `0`. The next `fast` call's row shows `attempts` 1, since the open breaker is skipped.
3. **Half-open.** Wait 30 s (`breaker.open_s`), then one more call. The breaker tries one trial request, fails, and goes back to `2`.
4. **Models list is stable.** `/v1/models` still lists `fast` and `smart` while the breaker is open.
5. **Attempts metric.** `metric gateway_upstream_attempts`. Expect `outcome="failed"` for openai and `"ok"` for anthropic.

**B. Everything down.** Also point Anthropic at a dead address:

```bash
sed -i '' 's#base_url: https://api.anthropic.com#base_url: https://127.0.0.1:9#' deploy/k8s/base/config.yaml && roll
```

6. `chat $KEY "$HI" | jq .error` gives `502` (all attempts failed). After about 6 calls both breakers are open and you get `503`.

**C. Timeouts.** Restore the providers, then set every timeout to 3 s:

```bash
git checkout deploy/k8s/base/config.yaml
sed -i '' -e 's/first_byte_s: 20 /first_byte_s: 3 /' -e 's/idle_s: 30 /idle_s: 3 /' -e 's/total_s: 300 /total_s: 3 /' deploy/k8s/base/config.yaml && roll
```

7. **Mid-stream cut.** Stream "Write 800 words about rivers" to `smart` (as in section 1). Expect chunks for about 3 s, then an SSE `error` event instead of `[DONE]`.
8. **Non-stream timeout.** The same prompt without `stream` returns `504` (`timeout_error`).

**Restore:** `git checkout deploy/k8s/base/config.yaml && roll`. Check `git diff deploy/k8s/base/config.yaml` is empty.

## 6. Cache

Only non-streaming requests that set `temperature: 0` are cached, for 300 s, per key.

```bash
T0='{"model":"smart","temperature":0,"messages":[{"role":"user","content":"Capital of France?"}]}'
cache() { curl -s -D - -o /dev/null $GW/v1/chat/completions -H "Authorization: Bearer $1" -H 'content-type: application/json' $3 -d "$2" | grep -i x-gateway-cache; }
```

1. **Miss, then hit.** `cache $KEY "$T0"; cache $KEY "$T0"`. Expect `miss`, then `hit`. The hit's usage row has `cache_hit` 1.
2. **Bypass.** `cache $KEY "$T0" "-H x-gateway-cache:bypass"`. Expect `bypass`.
3. **Not cacheable.** The same body without `temperature`, or with `"stream":true`, never shows `hit`.
4. **Scoped per key.** Make a second key and send `$T0`. Expect `miss`, since another tenant's answer is never reused.
5. **Hits still count against the rate limit.** With an `rpm_limit` 2 key, three cached calls in a row give a `429` on the third.
6. `metric gateway_cache_requests` shows the hit, miss and bypass counts.

## 7. Observability

1. **Health.** `curl -s $LOCAL/healthz; curl -s $LOCAL/readyz`. Expect both `200`.
2. **Metrics.** `curl -s $LOCAL/metrics | grep -E '^gateway_' | cut -d'{' -f1 | sort -u`. Expect requests, request duration, TTFT, in-flight, tokens, cost, rejected, auth failures, rate limited, budget exceeded, cache, upstream attempts, breaker state and usage counters.
3. **Low-cardinality labels.** `metric gateway_requests_total`. Labels are only `alias`, `provider` and `status_class`, never a key id or provider model id.
4. **Logs.** `kubectl -n llm-gateway logs deploy/gateway --tail=5`. Expect one JSON line per request with `request_id` (search for `runbook-1` from section 1). No prompt or completion text appears.
5. **Tracing** is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set, so there is nothing to check yet. The Grafana dashboard and alert rules in `deploy/observability/` need a Prometheus this cluster doesn't have yet.

## 8. Deployment checks

1. **Certificate.** `echo | openssl s_client -connect lmgate.nairvarun.com:443 -servername lmgate.nairvarun.com 2>/dev/null | openssl x509 -noout -subject -issuer -dates`. Expect subject `CN=lmgate.nairvarun.com`, an Amazon issuer and a future expiry date.
2. **TLS 1.3.** `curl -sI --tlsv1.3 $GW/v1/models | head -1` works. `curl -sI --tls-max 1.1 $GW/v1/models` fails the handshake.
3. **No plain HTTP.** `curl -s -m 5 http://lmgate.nairvarun.com/v1/models`. Expect a timeout or refusal, because there is no port 80 listener.
4. **DNS is not proxied.** `dig +short lmgate.nairvarun.com`. Expect the `…elb.amazonaws.com` name and AWS IPs, not Cloudflare's 104.x or 172.67.x.
5. **Graceful drain on rollout.** Start a long stream in one terminal ("Write 800 words about rivers" to `smart`), then in another run `kubectl -n llm-gateway rollout restart deploy/gateway`. Expect the stream to finish with `[DONE]`. K1 uses the Recreate strategy with one replica, so new requests get `503` or `502` until the new pod is ready. That gap is expected until K2.
6. **Data survives a restart.** After the rollout, `curl -s $LOCAL/admin/keys/$KEY_ID/usage -H "Authorization: Bearer $ADMIN"` (re-run the port-forward first) still shows earlier usage, because SQLite lives on the EBS volume.

## 9. Cleanup and teardown

1. **Revoke test keys.** Every key you made is revoked like this. `$KEY_ID` is shown; get the others' ids from the `mk` output.

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X DELETE $LOCAL/admin/keys/$KEY_ID -H "Authorization: Bearer $ADMIN"
   ```
2. **Config is clean.** `git diff deploy/k8s/base/config.yaml` prints nothing. If it does, `git checkout deploy/k8s/base/config.yaml && roll`.
3. **Stop the port-forward.** `kill %1`, or `pkill -f 'port-forward deploy/gateway'`.
4. **Tear down when you're done for the day.** Order matters, so the ALB and the EBS volume aren't orphaned:

   ```bash
   kubectl delete -k deploy/k8s/overlays/eks
   kubectl get pv            # wait until the volume is gone
   terraform -chdir=infra/eks destroy
   ```

   A later re-deploy creates a new ALB with a new hostname, so update the `lmgate` CNAME (DNS only) after it.

   The certificate is recreated too, so check that `terraform output acm_validation_record` still matches the validation record in Cloudflare.
