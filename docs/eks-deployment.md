# EKS staging deployment runbook

Status: **implementation and plan only; nothing in this runbook authorizes an
apply, image push, secret update, or deployment.** The exact account-specific
Terraform plan must be reviewed and explicitly approved before the first apply.

The staging design uses EKS 1.35, two private ARM64 managed nodes, a private
Kubernetes API by default, EKS access entries, EKS Pod Identity, the AWS Secrets
Store CSI provider, private RDS/Redis, and AWS PrivateLink endpoints. The gateway
remains mock-only and has no internet egress. Kubernetes manifests are rendered
only from Terraform outputs plus an immutable ECR digest; secret values never
enter the rendered files.

## Deployment gates

Before provisioning, confirm all of the following:

- The bootstrap and main Terraform plans, recurring EKS/EC2/RDS/Redis/endpoint
  costs, USD 100 provisional budget alert, backup/retention settings, initial
  EKS administrator principal, and private API access path have owner approval.
- Remote Terraform state has been activated and the main plan has been
  regenerated against it. A local-backend saved plan is never applied after
  changing the backend.
- A private network path exists from the operator to the Kubernetes API, or a
  specific non-world-open administrator CIDR has been reviewed and included in
  a fresh Terraform plan.
- Seven secret values are ready: a least-privilege application database URL,
  replay key, cache key, input-hash key, TLS certificate chain, and TLS private
  key, plus an independently generated `gw_` staging-smoke client key. The
  certificate must be valid for the internal service names clients use.
- The application image has been built for `linux/arm64`, tested, scanned after
  push, and selected by immutable registry digest.

Live model credentials and provider network egress are intentionally absent.
Passing this runbook does not authorize paid provider calls.

## 1. Provision only after explicit approval

Follow [the Terraform instructions](../infra/terraform/README.md). Apply the
state bootstrap first, activate the remote backend, generate a fresh main plan,
obtain approval for that exact plan, and only then apply it. Do not apply the
saved local-backend review plan.

The main apply creates the cluster and data foundation, not Kubernetes workload
objects. This separation avoids a Terraform Kubernetes provider depending on a
private API that does not exist at plan time.

## 2. Establish private cluster access

From an approved host with VPC connectivity and the reviewed IAM principal:

```sh
aws eks update-kubeconfig \
  --region ap-south-1 \
  --name llm-gateway-staging \
  --alias llm-gateway-staging
kubectl auth can-i get pods --all-namespaces
kubectl get nodes -o wide
kubectl get deployment -n kube-system
```

Verify two ready ARM64 nodes, the pinned core add-ons, Pod Identity agent, and
Secrets Store CSI provider. Do not solve an access failure by granting node IAM
permissions or embedding AWS credentials in a pod.

## 3. Create application credentials and populate external secrets

Create the `gateway` database role with only the privileges required by the
schema and migrations, using a controlled session inside the VPC. The URL stored
in `llm-gateway-staging/database-url` must use the private RDS endpoint and TLS,
for example the asyncpg `ssl=require` option. Do not run the API with the
RDS-managed master credential.

Populate each already-created Secrets Manager container from a permission-0600
local file so values do not appear in shell history:

```sh
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id llm-gateway-staging/database-url \
  --secret-string file://.local/staging/database-url
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id llm-gateway-staging/replay-key \
  --secret-string file://.local/staging/replay-key
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id llm-gateway-staging/cache-key \
  --secret-string file://.local/staging/cache-key
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id llm-gateway-staging/input-hash-key \
  --secret-string file://.local/staging/input-hash-key
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id llm-gateway-staging/smoke-client-key \
  --secret-string file://.local/staging/smoke-client-key
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id llm-gateway-staging/tls-cert \
  --secret-string file://.local/staging/tls-cert.pem
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id llm-gateway-staging/tls-key \
  --secret-string file://.local/staging/tls-key.pem
```

Use independently generated high-entropy values for the three application keys
and smoke client key. Keep the replay and cache keys stable across replicas and
ordinary rollouts; rotation needs an explicit compatibility/expiry procedure.
The smoke service account can read only its client key, database URL, and TLS
certificate; the API service account cannot read the smoke client key.

## 4. Build, push, scan, and select an immutable ARM64 image

After ECR exists and image publication is separately authorized:

```sh
AWS_ACCOUNT_ID=365712037872
AWS_REGION=ap-south-1
IMAGE_REPOSITORY="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/llm-gateway-staging"
IMAGE_TAG="$(git rev-parse HEAD)"

aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin \
    "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
docker buildx build --platform linux/arm64 \
  --tag "$IMAGE_REPOSITORY:$IMAGE_TAG" --push .
IMAGE_DIGEST="$(aws ecr describe-images --region "$AWS_REGION" \
  --repository-name llm-gateway-staging --image-ids imageTag="$IMAGE_TAG" \
  --query 'imageDetails[0].imageDigest' --output text)"
```

Review the enhanced/basic ECR scan result configured for the account and block
promotion on unresolved critical findings. Record the Git revision and digest;
tags are not deployment identity.

## 5. Render and inspect secret-free manifests

```sh
mkdir -p .local
terraform -chdir=infra/terraform output -json > .local/terraform-output.json
uv run python -m deploy.kubernetes.render \
  --terraform-output .local/terraform-output.json \
  --image-digest "$IMAGE_DIGEST"
rg '__[A-Z0-9_]+__|postgresql\+asyncpg://|BEGIN .*PRIVATE KEY' \
  .local/kubernetes/staging && exit 1 || true
```

Inspect every file under `.local/kubernetes/staging/`. Expected controls include
Pod Security `restricted`, a namespace default-deny policy plus bounded VPC
egress, Pod Identity-backed CSI mounts, HTTPS probes, non-root/read-only
containers, explicit resources, two replicas, topology spread, and a disruption
budget.

## 6. Apply prerequisites, run migrations, then roll out the API

```sh
MANIFESTS=.local/kubernetes/staging
kubectl apply -f "$MANIFESTS/00-namespace.yaml"
kubectl apply -f "$MANIFESTS/01-service-account.yaml"
kubectl apply -f "$MANIFESTS/02-secret-provider-class.yaml"
kubectl apply -f "$MANIFESTS/03-config-map.yaml"
kubectl apply -f "$MANIFESTS/04-network-policy.yaml"

kubectl delete job gateway-migrate -n llm-gateway --ignore-not-found
kubectl apply -f "$MANIFESTS/10-migration-job.yaml"
kubectl wait --for=condition=complete job/gateway-migrate \
  -n llm-gateway --timeout=5m
kubectl logs job/gateway-migrate -n llm-gateway

kubectl apply -f "$MANIFESTS/20-deployment.yaml"
kubectl apply -f "$MANIFESTS/21-service.yaml"
kubectl apply -f "$MANIFESTS/22-pod-disruption-budget.yaml"
kubectl rollout status deployment/gateway -n llm-gateway --timeout=5m
kubectl get pods,service,pdb -n llm-gateway -o wide
```

The migration Job is bounded and runs before replicas. Deployment pods set
`GATEWAY_RUN_MIGRATIONS=false`, so they cannot race schema changes. The Service
is private `ClusterIP` only; no public load balancer or DNS record is created.
Traffic inside the cluster is HTTPS on port 443, terminated by Uvicorn using the
externally supplied certificate.

## 7. Smoke, evaluation, and promotion evidence

Run the bounded smoke Job. It registers the externally generated synthetic key
by hash, calls the TLS readiness endpoint, then performs authenticated mock
generation and extraction. It never prints the client key:

```sh
kubectl delete job gateway-smoke -n llm-gateway --ignore-not-found
kubectl apply -f "$MANIFESTS/31-smoke-job.yaml"
kubectl wait --for=condition=complete job/gateway-smoke \
  -n llm-gateway --timeout=5m
kubectl logs job/gateway-smoke -n llm-gateway
```

Confirm readiness reports healthy PostgreSQL and Redis, and verify no
prompt/output/secret appears in logs.

Run the evaluation worker only after a run has been enqueued and its evidence
is required:

```sh
kubectl delete job gateway-evaluate -n llm-gateway --ignore-not-found
kubectl apply -f "$MANIFESTS/30-evaluation-job.yaml"
kubectl wait --for=condition=complete job/gateway-evaluate \
  -n llm-gateway --timeout=30m
kubectl logs job/gateway-evaluate -n llm-gateway
```

Promotion remains blocked until smoke, evaluation, security, scan, and sanitized
operational evidence are recorded. Synthetic evaluation does not validate live
provider quality or cost.

## Rollback

If readiness or smoke fails, preserve Job/pod logs and events, then stop
promotion. For an application-only failure with a backward-compatible schema:

```sh
kubectl rollout undo deployment/gateway -n llm-gateway
kubectl rollout status deployment/gateway -n llm-gateway --timeout=5m
```

Prefer re-rendering and applying the last known-good immutable digest so the
desired state is explicit. Never automatically reverse an Alembic migration.
If the new schema is not backward compatible, stop before migration; recovery
requires the reviewed restore/forward-fix procedure. Terraform rollback is not
`terraform destroy`: protected RDS, Secrets Manager, KMS, EKS, and artifact
resources have deletion guards or recovery windows.

## Known boundaries

- The current design is single-region staging, with single-AZ RDS and Redis.
- Cluster/API and node provisioning have not been exercised because apply is
  awaiting explicit approval.
- The private ClusterIP service is not internet-facing. Adding an AWS Load
  Balancer Controller, NLB/ALB, Route 53, or public ingress requires a separate
  reviewed IAM/network/certificate design and Terraform plan.
- Application log shipping, CloudWatch alarms, restore/load/upgrade drills, and
  public staging DNS remain unfinished milestone 6 evidence.
- Secrets Store CSI polls for updated certificate material, but Uvicorn does
  not hot-reload its TLS context; certificate rotation requires a controlled
  Deployment restart and verification.
- Evaluation S3 IAM is prepared, but application artifact upload is not yet
  implemented.
