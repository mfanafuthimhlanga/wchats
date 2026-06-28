# ---------------------------------------------------------------------------
# CloudFront — widget bundle CDN (PROD-08)
# Pitfall 2: ACM cert for CloudFront MUST be in us-east-1 (var.acm_certificate_arn_us_east_1)
# T-13-01-04: Origin Access Control (OAC) — no public S3 origin
# ---------------------------------------------------------------------------

# Managed CachingOptimized cache policy (AWS built-in, no TTL tuning needed)
data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

# Origin Access Control (OAC) — sigv4 signed requests to S3
resource "aws_cloudfront_origin_access_control" "widget" {
  name                              = "wchats-widget-oac"
  description                       = "OAC for W Chats widget S3 bundle origin"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# CloudFront distribution — widget bundle CDN
resource "aws_cloudfront_distribution" "widget" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "W Chats widget bundle CDN"
  default_root_object = "widget.js"
  aliases             = [var.widget_domain]
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.widget.bucket_regional_domain_name
    origin_id                = "wchats-widget-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.widget.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "wchats-widget-s3"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
  }

  # Pitfall 2: CloudFront viewer cert MUST use acm_certificate_arn_us_east_1
  # (a cert in any other region is rejected by CloudFront — see RESEARCH.md §Pitfall 2)
  viewer_certificate {
    acm_certificate_arn      = var.acm_certificate_arn_us_east_1
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  tags = {
    Name    = "wchats-widget-cdn"
    Project = "wchats"
  }
}
