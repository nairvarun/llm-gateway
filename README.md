# LLM Reliability Gateway (v2)

A small OpenAI-compatible LLM gateway built for learning: virtual keys, usage and cost tracking,
rate limits, budgets, timeouts, retries, fallback, a circuit breaker, metrics, caching, and a
Kubernetes deployment. Clients use any OpenAI SDK; the gateway routes to OpenAI or Anthropic.

- Spec: [docs/SPEC.md](docs/SPEC.md)
- High-level design: [docs/HLD.md](docs/HLD.md)
- Low-level design: [docs/LLD.md](docs/LLD.md)
- What each phase taught: [docs/notes/](docs/notes/)

## Run it locally

```bash
uv sync
export OPENAI_API_KEY=... ANTHROPIC_API_KEY=... ADMIN_TOKEN=$(openssl rand -hex 24)
export GATEWAY_CONFIG=deploy/k8s/base/config.yaml GATEWAY_DB_PATH=./gateway.db
uv run uvicorn gateway.app:create_app_from_env --factory --port 8080

# create a virtual key (the plaintext key is shown once)
curl -s -X POST localhost:8080/admin/keys -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'content-type: application/json' -d '{"name": "me", "rpm_limit": 60}'
```

Then point any OpenAI client at `http://localhost:8080/v1` with the `gw-...` key and a model
alias from the config (`fast`, `smart`).

The model IDs and prices in `deploy/k8s/base/config.yaml` are placeholders: verify them before
real use. The process refuses to start if a referenced provider key is missing.

## Test

```bash
uv run ruff check && uv run ruff format --check && uv run pytest -q
```

Integration tests run the gateway and a scriptable fake upstream on real sockets, so no
provider keys or network access are needed.

## Deploy (K1: single replica)

```bash
cp deploy/k8s/base/secrets.env.example deploy/k8s/base/secrets.env   # fill in; git-ignored
docker build -t llm-gateway:dev .
kubectl apply -k deploy/k8s/overlays/k1
kubectl -n llm-gateway port-forward deploy/gateway 8080   # /admin is not exposed by the Ingress
```

The cluster needs the M1.5 baseline from the spec: a default StorageClass, an ingress controller,
and Prometheus scraping (the pod carries `prometheus.io/*` annotations). The dashboard and alert
rules are in `deploy/observability/`.

### On AWS (EKS Auto Mode)

Terraform for the VPC, cluster and ECR repository is in [infra/eks](infra/eks/README.md), with
the cluster baseline in `deploy/k8s/cluster/eks-auto` and an `eks` overlay for the ALB and ECR.

## Status

| Milestone | State |
| --- | --- |
| Phases 0–6 (proxy, keys, usage, limits, resilience, observability, cache) | Built and tested |
| K1 deployment (Dockerfile, Kustomize base + k1 overlay) | Built; image and manifests verified locally, not yet run on a cluster |
| EKS Auto Mode infrastructure (Terraform, baseline, eks overlay) | Written and validated; not yet applied |
| K2 scale-out (Postgres store, Redis limiter, PDB, HPA) | Not started (M4) |
| Real provider keys, model IDs and prices | Pending |
