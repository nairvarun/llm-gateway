# Every taggable resource gets these through the provider's default_tags (versions.tf), plus a
# Name of its own. Add more with var.tags. Resources that Kubernetes creates later (the ALB) are
# tagged from the Ingress annotation in deploy/k8s/overlays/eks/kustomization.yaml.
locals {
  tags = merge(
    {
      Project     = var.name
      Environment = var.environment
      ManagedBy   = "terraform"
      Repository  = "nairvarun/llm-gateway"
    },
    var.tags,
  )
}
