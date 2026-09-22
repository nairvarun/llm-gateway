# Staging Terraform plan review — 2026-09-22

Status: **planned only; nothing applied**. The OpenSpec milestone 6.3 task is
still open because account-owner review and cost/retention approval have not
occurred. These plans are not authorization to provision.

Verification completed: `terraform fmt -check -recursive` and `terraform
validate` for both stacks; the refreshed account-specific EKS plan; OpenSpec
strict validation; Ruff formatting/lint and strict mypy; 279 tests with real
local PostgreSQL/Redis; rendering and YAML parsing of all 11 secret-free
Kubernetes manifest files; rebuilt ARM64 Lima container generation/extraction
smoke; and browser execution of readiness showing healthy database/Redis and
the mock provider. These checks do not prove AWS provisioning, Kubernetes API
acceptance, cluster policy enforcement, load, security, or restore behavior.

## Exact plan scope

The plans were generated with Terraform 1.16.3 and AWS provider 6.65.0 under
account `365712037872`, region `ap-south-1`. The provider has an explicit
account guard. The only AWS operations so far were read-only discovery and
Terraform planning.

| Plan | Result | Main resources |
| --- | --- | --- |
| `infra/terraform/bootstrap/review.tfplan` | 6 add, 0 change, 0 destroy | S3 state bucket, public-access block, encryption, versioning, TLS-only bucket policy, noncurrent-version lifecycle |
| `infra/terraform/review-eks.tfplan` | 78 add, 0 change, 0 destroy | Two-AZ VPC/subnets/routes/security groups/endpoints, EKS 1.35 control plane, two private ARM64 managed nodes, five pinned add-ons, access entry, API and smoke Pod Identity roles/associations, private RDS PostgreSQL and Redis, ECR, encrypted artifact bucket, seven empty Secrets Manager containers, KMS/control-plane logs, AWS Budget |

Plans are local, ignored binary files. Run `terraform show review.tfplan` in
each directory for the full proposed actions. The main plan uses EKS instead of
the superseded ECS design. It includes cluster/nodes but no Kubernetes workload
objects, public load balancer, DNS record, NAT gateway, or live-provider network
egress. Workloads are rendered after an approved apply from Terraform outputs
and an immutable image digest. Nothing has been deployed or exercised in AWS.

## Cost and retention review

The default monthly AWS Budget is a **provisional USD 100 alert threshold**,
not an enforceable spending cap. It currently covers the whole account, not
only this project. With no `budget_alert_email`, the plan creates no email
notification. The owner must choose an amount, recipient, and account-wide
versus project-scoped accounting before apply; project-scoped AWS cost tags
need separate activation and verification. AWS Budget cannot itself stop
resource charges or paid-provider requests.

Recurring cost drivers in the base plan are the EKS control plane, two on-demand
`t4g.medium` nodes with 30 GiB encrypted gp3 volumes, RDS `db.t4g.micro` plus
20–40 GB gp3 storage/backups, one Redis `cache.t4g.micro` node, seven interface
endpoints across two AZs, S3 storage/requests, Secrets Manager, KMS, and
CloudWatch. Usage-based charges vary; **no dollar estimate has been verified**.
Review the AWS pricing calculator or an account-specific estimate before
approval.

Retention defaults: CloudWatch logs 7 days; evaluation artifact current
objects 30 days and noncurrent versions 7 days; Redis snapshot 1 day; RDS
automated backups 7 days; noncurrent Terraform state versions 90 days. The
current Terraform state object and live RDS/Redis/Secrets Manager resources
persist until explicit managed removal. RDS and Redis have deletion guards;
RDS final snapshot is required. Secrets Manager has a 30-day recovery window.
Privacy/retention review must account for backups and final snapshots, not
just live records.

## Items not 100% done

- **Approval and state:** Owner has not reviewed/approved either plan or the
  budget/retention tradeoffs. Main remote state cannot be activated until the
  bootstrap bucket exists; both applies require separate explicit approval,
  and the main plan must be regenerated with the remote backend. Bootstrap
  local state needs a documented secure custody handoff.
- **Cluster and rollout:** EKS 1.35/add-on availability was discovered in
  `ap-south-1`, but the cluster and nodes do not exist. No private API access
  path has been exercised. No image has been built, scanned, pushed, or pinned
  by digest. Kubernetes manifests are implemented but have not been submitted
  to a cluster; no staging smoke, promotion, rollback, or restore has run.
- **Ingress:** The current Service is private `ClusterIP` only and uses an
  externally supplied TLS certificate at the pod. No public/internal AWS load
  balancer, public hostname, DNS record, or ACM integration is defined. Any such
  exposure needs a separate reviewed controller/IAM/network/certificate plan.
- **Secrets and database:** Terraform creates secret containers without secret
  values so plaintext never enters Terraform state. An operator must create
  scoped application DB credentials, populate and rotate seven secret values,
  and run migrations before activating the service. RDS-managed master
  credentials exist only if provisioned. This is not yet verified in AWS.
- **Security and resilience:** Single-AZ RDS/Redis are staging tradeoffs, not
  HA. Redis has TLS and network isolation but no application auth token yet.
  load-balancer access logging, WAF, VPC Flow Logs, secret rotation automation, restore
  drills, and a finished threat model/security review remain open. No private
  internet egress means live-provider calls are impossible here; adding it
  would require a separate authorized design and plan.
- **Application wiring:** Artifact S3 and workload IAM are provisioned for future
  evaluation artifacts, but the application has not been wired to use S3.
  Evaluation workers have a bounded opt-in Kubernetes Job, but scheduling and
  artifact upload are not implemented. Pod log shipping and resource alarms are
  not defined or tested.
- **Milestone 6 evidence:** OpenSpec tasks 6.1–6.7 remain unchecked until their
  respective threat model, image, plan review, deployment, promotion, load/
  restore, and release gates have evidence. Offline milestones 1–5 do not
  establish cloud readiness or live-model quality/cost performance.

See [Terraform instructions](../infra/terraform/README.md) for reproduction and
the [EKS runbook](eks-deployment.md) for the post-apply workload sequence.
