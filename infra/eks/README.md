# EKS Auto Mode for the gateway (K1)

Terraform for the AWS side: a VPC, an EKS Auto Mode cluster, its two IAM roles, and an ECR
repository. Kubernetes objects stay in `deploy/k8s` and are applied with `kubectl`, so
Terraform never needs cluster credentials.

## What gets created

| Resource | Notes |
| --- | --- |
| VPC (`terraform-aws-modules/vpc` 6.7) | 3 AZs, private /20s for nodes and pods, public /24s for the ALB, one NAT gateway |
| EKS cluster (Kubernetes 1.35) | Auto Mode: AWS runs nodes, VPC CNI, CoreDNS, kube-proxy, EBS CSI and the ALB controller |
| Node pools | `system` and `general-purpose` (built in; Auto Mode picks instance types) |
| IAM | Cluster role (5 managed policies) and node role (minimal worker policy + ECR pull) |
| Access | Whoever runs `terraform apply` gets cluster-admin; add others in `admin_principal_arns` |
| Logs | `audit` and `authenticator` to CloudWatch, 30-day retention |
| ECR | `llm-gateway`, immutable tags, scan on push, keeps the last 20 images |

## Before you apply

- **Cost.** This runs billable resources around the clock: the EKS control plane, the NAT
  gateway, the EC2 instances Auto Mode launches (plus its per-instance management fee), the ALB
  once the Ingress exists, and EBS. Check current prices for your region before applying, and
  `terraform destroy` when you are not using it.
- **Narrow `api_public_access_cidrs`** to your own IP; the default lets anyone reach the API
  endpoint (they still need valid AWS credentials).
- **TLS.** The ALB listens on plain HTTP, so virtual keys travel unencrypted. Until a
  certificate is added, narrow `alb.ingress.kubernetes.io/inbound-cidrs` in
  `deploy/k8s/overlays/eks/kustomization.yaml` to your IP, or use `kubectl port-forward`.

## Deploy

```bash
# 1. Infrastructure (about 15 minutes)
cd infra/eks
cp terraform.tfvars.example terraform.tfvars        # set region, narrow the CIDRs
terraform init
terraform plan -out tfplan
terraform apply tfplan
$(terraform output -raw configure_kubectl)           # aws eks update-kubeconfig ...

# 2. Cluster baseline: default StorageClass, ALB IngressClass, NetworkPolicy enforcement
cd ../..
kubectl apply -k deploy/k8s/cluster/eks-auto

# 3. Image: build for amd64 (also on Apple Silicon) and push to ECR
ECR=$(terraform -chdir=infra/eks output -raw ecr_repository_url)
REGION=$(terraform -chdir=infra/eks output -raw region)
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ECR%%/*}"
docker buildx build --platform linux/amd64 -t "$ECR:0.2.0" --push .
#    then set newName/newTag in deploy/k8s/overlays/eks/kustomization.yaml to "$ECR" / 0.2.0

# 4. Secrets and the gateway
cp deploy/k8s/base/secrets.env.example deploy/k8s/base/secrets.env   # fill in; git-ignored
kubectl apply -k deploy/k8s/overlays/eks
kubectl -n llm-gateway rollout status deploy/gateway
kubectl -n llm-gateway get ingress gateway           # ADDRESS is the ALB hostname (takes a few minutes)
```

Create a key through a port-forward, since `/admin` is not routed by the ALB:

```bash
kubectl -n llm-gateway port-forward deploy/gateway 8080 &
curl -s -X POST localhost:8080/admin/keys -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'content-type: application/json' -d '{"name": "me"}'
```

## Tear down (order matters)

Delete the Kubernetes objects first, so Auto Mode removes the ALB and the EBS volume it
created. Otherwise they are orphaned and the VPC cannot be deleted.

```bash
kubectl delete -k deploy/k8s/overlays/eks             # removes the ALB and the PVC's volume
kubectl get pv                                        # wait until the volume is gone
terraform -chdir=infra/eks destroy
```

## Notes

- **Upgrades.** Raise `kubernetes_version` one minor version at a time. `upgrade_policy` is
  `STANDARD`, so EKS upgrades the cluster itself at end of standard support rather than moving it
  into paid extended support.
- **Drain timing.** The ALB needs a few seconds to stop routing to a terminating pod. If you see
  errors during deploys, raise `shutdown.drain_delay_s` (default 10) to 15–20 and
  `terminationGracePeriodSeconds` with it.
- **State.** State is local. For shared use, enable the S3 backend block in `versions.tf`.
- **K2.** The HPA needs metrics-server, which Auto Mode does not include; add it with K2.
