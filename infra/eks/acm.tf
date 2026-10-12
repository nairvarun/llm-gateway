# TLS certificate for the gateway's public hostname, validated through DNS. The DNS lives in
# Cloudflare, so Terraform only outputs the validation record; once it is added there, ACM
# issues the certificate within minutes. Nothing here waits for that, so apply never hangs.

resource "aws_acm_certificate" "gateway" {
  count             = var.domain_name == "" ? 0 : 1
  domain_name       = var.domain_name
  validation_method = "DNS"

  tags = { Name = var.domain_name }

  lifecycle {
    create_before_destroy = true
  }
}
