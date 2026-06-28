# ---------------------------------------------------------------------------
# S3 Buckets — widget bundle origin + document uploads
# T-13-01-04: Block Public Access ON for both buckets; no anonymous grants.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Bucket A: Widget bundle origin (CloudFront OAC — not public)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "widget" {
  bucket = "wchats-widget"

  tags = {
    Name    = "wchats-widget"
    Project = "wchats"
  }
}

resource "aws_s3_bucket_versioning" "widget" {
  bucket = aws_s3_bucket.widget.id

  versioning_configuration {
    status = "Enabled"
  }
}

# T-13-01-04: Block ALL public access — widget reachable only via CloudFront OAC
resource "aws_s3_bucket_public_access_block" "widget" {
  bucket = aws_s3_bucket.widget.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# OAC bucket policy: allow s3:GetObject ONLY from the CloudFront distribution
# via the CloudFront service principal + AWS:SourceArn condition (no public grant)
resource "aws_s3_bucket_policy" "widget" {
  bucket = aws_s3_bucket.widget.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudFrontOACRead"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.widget.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.widget.arn
          }
        }
      }
    ]
  })

}

# ---------------------------------------------------------------------------
# Bucket B: Document uploads (Fargate task role access only — not public)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "uploads" {
  bucket = "wchats-uploads"

  tags = {
    Name    = "wchats-uploads"
    Project = "wchats"
  }
}

resource "aws_s3_bucket_versioning" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  versioning_configuration {
    status = "Enabled"
  }
}

# T-13-01-04: Block ALL public access — uploads reachable only via IAM task role
resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Server-side encryption at rest (AES256)
resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# NO bucket policy on the uploads bucket — access is exclusively via
# aws_iam_role.task (iam.tf S3Uploads statement). Tenant isolation is by
# key prefix {agent_id}/... (agent UUIDv4 ~122-bit entropy — RESEARCH.md §Security).
