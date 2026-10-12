# Handover

State of the project at hand-off (2026-10-12), what to do next, and what a new session needs to
know about this environment. Branch: `v2`. Everything below is committed and pushed.

## Read first

| Doc | What it is |
| --- | --- |
| [SPEC.md](SPEC.md) | The plan: scope, phases, acceptance criteria, decisions D1–D22, milestones |
| [HLD.md](HLD.md) | High-level design with diagrams |
| [LLD.md](LLD.md) | Low-level design: types, stages, algorithms, tests, Terraform; ends with implementation notes |
| [notes/](notes/) | One learning note per phase |
| [../infra/eks/README.md](../infra/eks/README.md) | AWS deploy runbook, Cloudflare DNS records, teardown order |
| [video/README.md](video/README.md) | How the explainer video is built |

Editable online copies exist ([HLD](https://claude.ai/code/artifact/95848d23-9e1f-4244-9978-0342112086ae),
[LLD](https://claude.ai/code/artifact/2790d2f0-dd28-4d52-a97c-6fa7fff452dc)), but they lag the repo: they
lack the LLD implementation notes and still describe the HTTP-only interim plan. **The repo copies win.**

## What is built

| Area | State | Evidence |
| --- | --- | --- |
| Gateway, phases 0–6 (streaming proxy, OpenAI + Anthropic, virtual keys, usage/cost, rate limits, budgets, retries/fallback/breaker, metrics/logs/tracing, cache) | Done | 74 tests pass (`uv run pytest -q`, ~28 s); ruff clean; core ~1,750 non-blank lines |
| Docker image | Done | Built and run locally (non-root, read-only rootfs) against the fake upstream |
| Kustomize base, `k1` overlay, `eks` overlay, EKS Auto Mode cluster baseline | Written | Rendered with kustomize; never applied to a cluster |
| Terraform for EKS Auto Mode (`infra/eks/`): VPC, cluster, IAM, access, logs, ECR, ACM certificate | Written | `terraform fmt` + `validate` pass; **never planned or applied** |
| HTTPS at `lmgate.nairvarun.com` | Written | ACM cert (DNS validation) + HTTPS-only ALB listener; waits on the Cloudflare records |
| Dashboard and alert rules (`deploy/observability/`) | Written | Never loaded into Grafana/Prometheus |
| Explainer video (`docs/video/`) | Done | 17-minute MP4, voice `am_michael`; output is git-ignored |

Nothing has been tested against the real OpenAI or Anthropic APIs. Model IDs and prices in
`deploy/k8s/base/config.yaml` are **placeholders**.

## Decisions already made (do not reopen)

The spec's decision table (D1–D22) is authoritative. The ones most likely to come up:
- Drain starts from a `preStop` call to loopback-only `POST /internal/drain` (D13).
- Client disconnects are stored as status 499 (D14).
- Temperature passes through; values above 1 are clamped to 1 for Anthropic (D15).
- K2 autoscaling uses CPU (D16); `/v1/models` is stable regardless of breaker state (D17).
- EKS Auto Mode; Terraform for AWS only, Kustomize for in-cluster objects (D18, D19).
- HTTPS only at `lmgate.nairvarun.com`; both Cloudflare records **DNS only**, because the proxy's
  100-second timeout would cut long streams (D22).
- Secrets never go into git or chat. `deploy/k8s/base/secrets.env` exists only on the machine
  that runs `kubectl apply`.

## Next steps

### 1. Live smoke test with the real providers (this is what the new session is for)

The user added two environment variables to this environment: `LMGATE_OPENAI_API_KEY` and
`LMGATE_ANTHROPIC_API_KEY`. Check they are visible without printing them:

```bash
for v in LMGATE_OPENAI_API_KEY LMGATE_ANTHROPIC_API_KEY; do [ -n "${!v}" ] && echo "$v set" || echo "$v MISSING"; done
```

If they are missing, stop and tell the user (they must be set in the environment settings, and
only new sessions see them). Never echo, log or commit their values.

Then:
1. **Verify real model IDs and prices** from the providers' current documentation (fetch the pages;
   do not rely on memory) for a cheap and a capable model on each provider.
2. **Make a local config** that is not committed, pointing at the real keys:
   ```bash
   sed -e 's/api_key_env: OPENAI_API_KEY/api_key_env: LMGATE_OPENAI_API_KEY/' \
       -e 's/api_key_env: ANTHROPIC_API_KEY/api_key_env: LMGATE_ANTHROPIC_API_KEY/' \
       deploy/k8s/base/config.yaml > /tmp/live-config.yaml     # then put the verified models/prices in it
   ```
   Note: `providers.*.api_key_env` names which variable holds each key, so the `LMGATE_` names
   work without renaming anything.
3. **Run the gateway** and exercise it:
   ```bash
   export ADMIN_TOKEN=$(openssl rand -hex 24) GATEWAY_CONFIG=/tmp/live-config.yaml GATEWAY_DB_PATH=/tmp/live.db
   uv run uvicorn gateway.app:create_app_from_env --factory --port 8080 &
   KEY=$(curl -s -X POST localhost:8080/admin/keys -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H 'content-type: application/json' -d '{"name":"smoke","monthly_budget_usd":1}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["key"])')
   ```
   Check, for both providers: a non-streaming call; a streaming call (`"stream": true`, tokens arrive
   incrementally, ends with `[DONE]`); the usage row in `/tmp/live.db` has real token counts and a
   cost that matches the verified prices; fallback on the `fast` alias. Keep `max_tokens` small;
   the budget above caps spend at $1.
4. **Commit the verified model IDs and prices** to `deploy/k8s/base/config.yaml` (and to the
   example in the spec if it changed). Anthropic's live SSE events may differ from the recorded
   fixtures in `tests/providers/test_adapters.py`; if so, fix the adapter and add a fixture.

### 2. CI (unblocked)

GitHub Actions on push: `uv run ruff check`, `uv run ruff format --check`, `uv run pytest -q`,
`docker build`, `terraform -chdir=infra/eks fmt -check` and `validate` (with `-backend=false`).

### 3. Waiting on the user

| Item | Notes |
| --- | --- |
| `terraform apply` from their machine | Fill `infra/eks/terraform.tfvars`: `region`, `domain_name = "lmgate.nairvarun.com"`, `api_public_access_cidrs = ["<their IP>/32"]` (`curl -s https://checkip.amazonaws.com`) |
| Cloudflare records (both CNAME, DNS only) | `terraform output acm_validation_record` after apply; then `lmgate` → the ALB hostname from `kubectl -n llm-gateway get ingress gateway` after deploy |
| `secrets.env` on their machine | Provider keys + an `ADMIN_TOKEN` of at least 32 characters |
| Set image tag and `certificate-arn` in `deploy/k8s/overlays/eks/kustomization.yaml` | From `ecr_repository_url` and `certificate_arn` outputs |

### 4. After the cluster exists

End-to-end through the ALB (health check, long streams, drain during a deploy); a Prometheus stack
(self-run or Amazon Managed Prometheus) and loading the dashboard and alerts; a k6 load test to set
resource requests and limits; chaos runs (kill upstream and pod mid-stream).

### 5. Later (M4)

K2: `store/postgres.py`, `limiter/redis.py` (Lua token bucket), a `k2` overlay with RollingUpdate,
PDB, HPA on CPU, and metrics-server.

## Environment notes (cloud container)

Learned the hard way in the previous session:

- **Docker daemon is not running by default.** Start it with `sudo dockerd &` (wait for `docker info`).
- **Docker Hub rate-limits (429).** Pull through the mirror and retag:
  `docker pull mirror.gcr.io/library/python:3.13-slim && docker tag mirror.gcr.io/library/python:3.13-slim python:3.13-slim`.
- **TLS goes through an egress proxy** (CA bundle at `/root/.ccr/ca-bundle.crt`). `docker build`
  fails at `uv sync` with "UnknownIssuer". Build from a scratch copy whose build stage copies the CA
  and sets `SSL_CERT_FILE`, with `--network=host --build-arg HTTPS_PROXY=$HTTPS_PROXY`. Do not
  change the committed Dockerfile for this.
- **No kubectl or kustomize installed.** Render with
  `docker run --rm -v $PWD/deploy/k8s:/k registry.k8s.io/kustomize/kustomize:v5.4.3 build /k/overlays/eks`
  (copy `secrets.env.example` to `secrets.env` first, then delete it).
- **Terraform is not preinstalled.** Download from `releases.hashicorp.com` into `~/.local/bin`.
- **Chromium** for Playwright is at `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`
  (`CHROME_PATH` for the video build).
- **`ruff format` also rewrites Python blocks inside Markdown**; `docs/` is excluded in
  `pyproject.toml` for that reason.
- **No AWS credentials here, by design.** The user runs Terraform and kubectl from their machine.

## Repository map

```
gateway/            app code (stages/, providers/, resilience/, store/, limiter/)
tests/              unit/, stages/, providers/, integration/ (real sockets), fakes/
deploy/k8s/         base/ (incl. config.yaml), overlays/{k1,eks}, cluster/eks-auto
deploy/observability/  Grafana dashboard, alert rules
infra/eks/          Terraform (EKS Auto Mode, VPC, IAM, ECR, ACM)
docs/               SPEC, HLD, LLD, HANDOVER, notes/, img/, video/
```
