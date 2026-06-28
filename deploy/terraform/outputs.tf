output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer (use in Route53 alias record or for smoke testing)"
  value       = aws_lb.main.dns_name
}

output "api_url" {
  description = "Stable HTTPS API URL (Route53 alias → ALB). The data-api value in the embed snippet."
  value       = "https://${var.api_domain}"
}

output "widget_cdn_url" {
  description = "Widget CDN URL (CloudFront custom domain). The script src in the embed snippet."
  value       = "https://${var.widget_domain}"
}

output "ecr_api_repository_url" {
  description = "ECR repository URL for the API/runtime worker image. Use in docker buildx push."
  value       = aws_ecr_repository.api.repository_url
}

output "ecr_pipeline_repository_url" {
  description = "ECR repository URL for the pipeline worker image. Use in docker buildx push."
  value       = aws_ecr_repository.pipeline.repository_url
}

output "elasticache_primary_endpoint" {
  description = "ElastiCache Redis primary endpoint. Use this in the wchats/redis-url secret (rediss://<endpoint>:6379/0)."
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "uploads_bucket_name" {
  description = "S3 bucket name for document uploads. Use this in the S3_UPLOADS_BUCKET environment variable."
  value       = aws_s3_bucket.uploads.bucket
}

output "widget_bucket_name" {
  description = "S3 bucket name for the widget bundle origin. Upload apps/admin/public/wchats/ here after apply."
  value       = aws_s3_bucket.widget.bucket
}
