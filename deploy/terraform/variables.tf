variable "aws_region" {
  description = "AWS region for all resources (us-east-1 aligns with Neon REGION and widest Bedrock model availability)"
  type        = string
  default     = "us-east-1"
}

variable "api_domain" {
  description = "Stable production API domain (e.g. api.wchats.app). Route53 A-alias record points here."
  type        = string
}

variable "widget_domain" {
  description = "Widget CDN custom domain (e.g. widget.wchats.app). CloudFront distribution serves the widget bundle here."
  type        = string
}

variable "route53_zone_id" {
  description = "Route53 hosted zone ID for the apex domain that owns api_domain and widget_domain."
  type        = string
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the ALB HTTPS listener (must cover api_domain; can be in any region, typically same as aws_region)."
  type        = string
}

variable "acm_certificate_arn_us_east_1" {
  description = "ACM certificate ARN for the CloudFront viewer certificate. MUST be in us-east-1 regardless of aws_region — CloudFront requirement (Pitfall 2)."
  type        = string
}

variable "api_image_tag" {
  description = "ECR image tag for the API and runtime worker image (wchats-api repository)."
  type        = string
  default     = "latest"
}

variable "pipeline_image_tag" {
  description = "ECR image tag for the pipeline worker image (wchats-pipeline repository)."
  type        = string
  default     = "latest"
}
