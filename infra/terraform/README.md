# Staging Terraform

This is an **unapplied** AWS staging design. It has two independent plans:

1. `bootstrap/` provisions the private, versioned, encrypted S3 bucket for
   Terraform state. Bootstrap necessarily starts with local state; keep that
   file private and transfer custody before any approved apply.
2. This directory provisions the staging network, data services, secrets
   *containers*, ECR, roles, and optional runtime. Its checked-in
   `backend.tf.example` is deliberately inactive until the bootstrap bucket
   exists. The current review plan uses local backend mode and must be
   regenerated after backend activation; do not apply a stale saved plan.

No Terraform apply, image push, provider call, deployment, or cloud resource
creation has been authorized by this document. See the
[account-specific plan review](../../docs/staging-plan-review.md) before
requesting approval.

## Reproduce the read-only plans

Use reviewed AWS credentials for account `365712037872` in `ap-south-1`. The
provider refuses a different account. These commands read AWS data and save
local plan files; they do **not** apply changes:

```sh
cd infra/terraform/bootstrap
terraform init -backend=false -input=false
terraform fmt -check -recursive
terraform validate
terraform plan -input=false -lock=false -out=review.tfplan \
  -var='expected_account_id=365712037872'
terraform show review.tfplan

cd ..
terraform init -backend=false -input=false
terraform fmt -check -recursive
terraform validate
terraform plan -input=false -lock=false -out=review.tfplan \
  -var='expected_account_id=365712037872'
terraform show review.tfplan
```

Plan files and local state are ignored by Git because they can contain
sensitive values. Do not send them through chat or public artifacts. A plan
is a point-in-time proposal, not proof that AWS will accept all creates.

## Later activation sequence — each apply requires owner approval

After reviewing both plans, costs, retention, and the open decisions, obtain
explicit approval for **each** apply. Bootstrap the state bucket first. Before
any main apply, create `backend.tf` from `backend.tf.example` (ignored by Git),
then initialize the main directory with the reviewed state bucket, region,
key, and S3 lockfile. Store bootstrap local state under an approved secure
custody procedure; do not commit or discard it. Re-plan the main stack against
that backend and obtain review of that **new** plan before applying it. Never
reuse this local-backend review plan for the remote-backend deployment.

The base plan deliberately has `enable_runtime=false`, no image digest, no
ACM certificate, no ECS service, and no public ALB. Runtime activation needs
an approved hostname and regional certificate, immutable ARM64 image digest,
populated secret versions, a least-privilege database application user,
successful one-off migrations, and a fresh plan/review. The public listener
is HTTPS-only. No NAT or outbound internet path exists from private tasks;
paid provider dispatch cannot work in this network design and remains
separately prohibited until authorized and designed.
