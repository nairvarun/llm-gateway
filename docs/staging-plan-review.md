# Staging Terraform plan review — 2026-09-21

Status: **planned only; nothing applied**. The OpenSpec milestone 6.3 task is
still open because account-owner review and cost/retention approval have not
occurred. These plans are not authorization to provision.

Verification completed: `terraform fmt -check -recursive` and `terraform
validate` for both stacks; both account-specific plans; OpenSpec strict
validation; Ruff formatting/lint, mypy, and 272 local tests; rebuilt Lima
container generation/extraction smoke; and browser loading of the current API
documentation. These checks do not prove AWS provisioning, load, security, or
restore behavior.

## Exact plan scope

The plans were generated with Terraform 1.16.3 and AWS provider 6.65.0 under
account `365712037872`, region `ap-south-1`. The provider has an explicit
account guard. The only AWS operations so far were read-only discovery and
Terraform planning.

| Plan | Result | Main resources |
| --- | --- | --- |
| `infra/terraform/bootstrap/review.tfplan` | 6 add, 0 change, 0 destroy | S3 state bucket, public-access block, encryption, versioning, TLS-only bucket policy, noncurrent-version lifecycle |
| `infra/terraform/review.tfplan` | 63 add, 0 change, 0 destroy | Two-AZ VPC/subnets/routes/security groups/endpoints, private RDS PostgreSQL and Redis, ECR, encrypted private artifact bucket, four empty Secrets Manager containers, IAM roles, ECS cluster, CloudWatch logs, AWS Budget |

Plans are local, ignored binary files. Run `terraform show review.tfplan` in
each directory for the full proposed actions. No current plan includes a
running ECS service, task definition, ALB, HTTPS listener, NAT gateway, DNS
record, or live-provider network egress. A regional ACM certificate and an
immutable image digest were not found/supplied, so activation is gated by
variables and requires a new plan. Nothing has been deployed or exercised in
AWS.

## Cost and retention review

The default monthly AWS Budget is a **provisional USD 100 alert threshold**,
not an enforceable spending cap. It currently covers the whole account, not
only this project. With no `budget_alert_email`, the plan creates no email
notification. The owner must choose an amount, recipient, and account-wide
versus project-scoped accounting before apply; project-scoped AWS cost tags
need separate activation and verification. AWS Budget cannot itself stop
resource charges or paid-provider requests.

Recurring cost drivers in the base plan are RDS `db.t4g.micro` plus 20–40 GB
gp3 storage/backups, one Redis `cache.t4g.micro` node, four private interface
endpoints across two AZs, S3 storage/requests, Secrets Manager, and CloudWatch.
If activated later, an ALB and Fargate tasks add recurring cost. Usage-based
charges vary; **no dollar estimate has been verified**. Review the AWS pricing
calculator or an account-specific estimate before approval.

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
- **Ingress and rollout:** No staging hostname, DNS zone/record, or regional
  ACM certificate is available. No image has been built, scanned, pushed, or
  pinned by digest. No ECS task/service is in the current plan, and no staging
  smoke, promotion, rollback, or restore has been exercised.
- **Secrets and database:** Terraform creates secret containers without secret
  values so plaintext never enters Terraform state. An operator must create
  scoped application DB credentials, populate and rotate four secret values,
  and run migrations before activating the service. RDS-managed master
  credentials exist only if provisioned. This is not yet verified in AWS.
- **Security and resilience:** Single-AZ RDS/Redis are staging tradeoffs, not
  HA. Redis has TLS and network isolation but no application auth token yet.
  ALB access logging, WAF, VPC Flow Logs, secret rotation automation, restore
  drills, and a finished threat model/security review remain open. No private
  internet egress means live-provider calls are impossible here; adding it
  would require a separate authorized design and plan.
- **Application wiring:** Artifact S3 and task IAM are provisioned for future
  evaluation artifacts, but the application has not been wired to use S3.
  Evaluation workers are task definitions only after image activation and
  have no scheduling/orchestration in this plan. CloudWatch alarms for the
  staging resources are not defined or tested yet.
- **Milestone 6 evidence:** OpenSpec tasks 6.1–6.7 remain unchecked until their
  respective threat model, image, plan review, deployment, promotion, load/
  restore, and release gates have evidence. Offline milestones 1–5 do not
  establish cloud readiness or live-model quality/cost performance.

See [Terraform instructions](../infra/terraform/README.md) for reproduction.
