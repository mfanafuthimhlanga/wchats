# ---------------------------------------------------------------------------
# Route53 DNS Records — stable production domains (PROD-10)
#
# api_domain  → A alias → ALB
# widget_domain → A alias → CloudFront distribution (declared in cloudfront.tf)
# ---------------------------------------------------------------------------

# Stable API domain (PROD-10): A alias → ALB DNS name
# This is the value used in data-api="https://<api_domain>" of the embed snippet.
resource "aws_route53_record" "api" {
  zone_id = var.route53_zone_id
  name    = var.api_domain
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}

# Widget CDN domain: A alias → CloudFront distribution
# (aws_cloudfront_distribution.widget is declared in cloudfront.tf)
resource "aws_route53_record" "widget" {
  zone_id = var.route53_zone_id
  name    = var.widget_domain
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.widget.domain_name
    zone_id                = aws_cloudfront_distribution.widget.hosted_zone_id
    evaluate_target_health = false
  }
}
