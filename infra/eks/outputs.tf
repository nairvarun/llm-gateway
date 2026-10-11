output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "region" {
  value = var.region
}

output "cluster_endpoint" {
  value = aws_eks_cluster.this.endpoint
}

output "configure_kubectl" {
  description = "Run this to point kubectl at the new cluster."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.this.name}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.gateway.repository_url
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "certificate_arn" {
  description = "ACM certificate for domain_name; goes into the eks overlay's certificate-arn annotation."
  value       = try(aws_acm_certificate.gateway[0].arn, null)
}

output "acm_validation_record" {
  description = "Add this CNAME in Cloudflare (DNS only) so ACM can issue the certificate."
  value = try({
    for o in aws_acm_certificate.gateway[0].domain_validation_options : o.domain_name => {
      type  = o.resource_record_type
      name  = o.resource_record_name
      value = o.resource_record_value
    }
  }, null)
}
