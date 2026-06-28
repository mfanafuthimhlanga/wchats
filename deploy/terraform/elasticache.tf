# ---------------------------------------------------------------------------
# Amazon ElastiCache Redis
# PROD-03: Drop-in replacement for Upstash — same REDIS_URL env var, rediss:// URL.
# T-13-01-05: transit_encryption_enabled=true; SG restricts to ECS task SG only.
# ---------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "main" {
  name        = "wchats-elasticache-subnet-group"
  description = "Private subnets for W Chats ElastiCache Redis"
  subnet_ids  = [aws_subnet.private_a.id, aws_subnet.private_b.id]

  tags = {
    Name    = "wchats-elasticache-subnet-group"
    Project = "wchats"
  }
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "wchats-redis"
  description          = "W Chats ElastiCache Redis — Celery broker and SSE pub/sub channel"

  node_type          = "cache.t3.micro"
  num_cache_clusters = 1
  engine             = "redis"
  engine_version     = "7.1"
  port               = 6379

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.elasticache.id]

  # T-13-01-05: transit encryption required; CERT_NONE is acceptable in-VPC
  # (network isolation is the primary boundary; see RESEARCH.md §Cluster 2)
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true

  # Single-node: automatic failover is not supported with num_cache_clusters = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false

  apply_immediately = true

  tags = {
    Name    = "wchats-redis"
    Project = "wchats"
  }
}
