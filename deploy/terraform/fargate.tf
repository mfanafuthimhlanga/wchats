# ---------------------------------------------------------------------------
# ECS Fargate — cluster, CloudWatch log groups, three task definitions and services
#
# Services:
#   1. API (wchats-api): uvicorn, 0.5 vCPU / 1 GB, desired=2 (HA), ALB-attached
#   2. Runtime worker (always-on): celery runtime queue, 2 vCPU / 4 GB, desired=1
#      prefork+concurrency=2 (PROD-15: raised from 1 after 13-07 ContextVar refactor)
#   3. Pipeline worker (Fargate Spot): celery pipeline queue, 2 vCPU / 8 GB, desired=1
#      acks_late=True + idempotency (CLAUDE.md) make Spot interruption safe
#
# PROD-07: Secrets injected via Secrets Manager ARN refs only — no literal in task def.
# Landmine 3: --pool=prefork in CMD overrides worker_pool="solo" from celery_app.py.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CloudWatch Log Groups
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/wchats-api"
  retention_in_days = 30

  tags = {
    Name    = "wchats-api-logs"
    Project = "wchats"
  }
}

resource "aws_cloudwatch_log_group" "runtime_worker" {
  name              = "/ecs/wchats-runtime-worker"
  retention_in_days = 30

  tags = {
    Name    = "wchats-runtime-worker-logs"
    Project = "wchats"
  }
}

resource "aws_cloudwatch_log_group" "pipeline_worker" {
  name              = "/ecs/wchats-pipeline-worker"
  retention_in_days = 30

  tags = {
    Name    = "wchats-pipeline-worker-logs"
    Project = "wchats"
  }
}

# ---------------------------------------------------------------------------
# ECS Cluster
# ---------------------------------------------------------------------------

resource "aws_ecs_cluster" "main" {
  name = "wchats"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name    = "wchats"
    Project = "wchats"
  }
}

# Associate FARGATE and FARGATE_SPOT capacity providers with the cluster
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    base              = 0
    weight            = 1
    capacity_provider = "FARGATE"
  }
}

# ---------------------------------------------------------------------------
# Shared locals — secrets and environment blocks reused across all three services
# ---------------------------------------------------------------------------

locals {
  # Required secrets injected via Secrets Manager ARN refs (PROD-07; T-13-01-01)
  task_secrets = [
    { name = "NEON_API_KEY",                valueFrom = aws_secretsmanager_secret.neon_api_key.arn },
    { name = "NEON_ENCRYPTION_KEY",         valueFrom = aws_secretsmanager_secret.neon_encryption_key.arn },
    { name = "CONTROL_DB_URL",              valueFrom = aws_secretsmanager_secret.control_db_url.arn },
    { name = "CONTROL_DB_SYNC_URL",         valueFrom = aws_secretsmanager_secret.control_db_sync_url.arn },
    { name = "REDIS_URL",                   valueFrom = aws_secretsmanager_secret.redis_url.arn },
    { name = "ADMIN_KEY",                   valueFrom = aws_secretsmanager_secret.admin_key.arn },
    { name = "ANTHROPIC_API_KEY",           valueFrom = aws_secretsmanager_secret.anthropic_api_key.arn },
    # VOYAGE_API_KEY: required until 13-02 (Bedrock migration) makes it optional
    { name = "VOYAGE_API_KEY",              valueFrom = aws_secretsmanager_secret.voyage_api_key.arn },
    { name = "JWT_SECRET",                  valueFrom = aws_secretsmanager_secret.jwt_secret.arn },
    { name = "CLERK_WEBHOOK_SIGNING_SECRET", valueFrom = aws_secretsmanager_secret.clerk_webhook_signing_secret.arn },
  ]

  # Non-secret config — safe to expose in plaintext (PROD-07 contract)
  task_environment = [
    { name = "ENVIRONMENT",           value = "production" },
    { name = "AWS_REGION",            value = var.aws_region },
    { name = "NEON_REGION",           value = "aws-us-east-1" },
    { name = "BEDROCK_EMBED_MODEL_ID", value = "amazon.titan-embed-text-v2:0" },
    { name = "EMBEDDING_PROVIDER",    value = "bedrock" },
    { name = "S3_UPLOADS_BUCKET",     value = aws_s3_bucket.uploads.bucket },
    { name = "CORS_ORIGINS",          value = jsonencode(["https://${var.widget_domain}"]) },
  ]
}

# ---------------------------------------------------------------------------
# Service 1: API (uvicorn) — always-on, ALB-attached, desired=2 for HA
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "api" {
  family                   = "wchats-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
      essential = true

      command = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        }
      ]

      environment = local.task_environment
      secrets     = local.task_secrets

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = {
    Name    = "wchats-api"
    Project = "wchats"
  }
}

resource "aws_ecs_service" "api" {
  name            = "wchats-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  # Give the API container 120s before ALB marks it unhealthy (uvicorn startup + SDK import)
  health_check_grace_period_seconds = 120

  network_configuration {
    subnets          = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [
    aws_lb_listener.https,
    aws_iam_role_policy_attachment.task_execution_managed,
  ]

  tags = {
    Name    = "wchats-api"
    Project = "wchats"
  }
}

# ---------------------------------------------------------------------------
# Service 2: Runtime Celery worker — always-on, no ALB, runtime queue
# concurrency=2 (PROD-15): safe now that 13-07 ContextVar refactor landed.
# agent_tools.py globals replaced with ContextVars — no cross-request state bleed
# at concurrency > 1.  --pool=prefork overrides worker_pool config in celery_app.py.
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "runtime_worker" {
  family                   = "wchats-runtime-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "2048"
  memory                   = "4096"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "runtime-worker"
      image     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
      essential = true

      command = [
        "celery", "-A", "app.worker.celery_app", "worker",
        "--queues=runtime",
        "--pool=prefork",
        "--concurrency=2",
        "--hostname=runtime@%h",
        "--loglevel=info",
      ]

      environment = local.task_environment
      secrets     = local.task_secrets

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.runtime_worker.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = {
    Name    = "wchats-runtime-worker"
    Project = "wchats"
  }
}

resource "aws_ecs_service" "runtime_worker" {
  name            = "wchats-runtime-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.runtime_worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  depends_on = [
    aws_iam_role_policy_attachment.task_execution_managed,
  ]

  tags = {
    Name    = "wchats-runtime-worker"
    Project = "wchats"
  }
}

# ---------------------------------------------------------------------------
# Service 3: Pipeline Celery worker — Fargate Spot, pipeline queue
# acks_late=True + idempotency (CLAUDE.md rule 5) make Spot interruption safe.
# Landmine 3: --pool=prefork overrides worker_pool="solo" from celery_app.py
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "pipeline_worker" {
  family                   = "wchats-pipeline-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "2048"
  memory                   = "8192"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "pipeline-worker"
      image     = "${aws_ecr_repository.pipeline.repository_url}:${var.pipeline_image_tag}"
      essential = true

      command = [
        "celery", "-A", "app.worker.celery_app", "worker",
        "--queues=pipeline",
        "--pool=prefork",
        "--concurrency=1",
        "--hostname=pipeline@%h",
        "--loglevel=info",
      ]

      environment = local.task_environment
      secrets     = local.task_secrets

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.pipeline_worker.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = {
    Name    = "wchats-pipeline-worker"
    Project = "wchats"
  }
}

resource "aws_ecs_service" "pipeline_worker" {
  name            = "wchats-pipeline-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.pipeline_worker.arn
  desired_count   = 1
  # No launch_type when using capacity_provider_strategy

  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
    base              = 0
  }

  network_configuration {
    subnets          = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  depends_on = [
    aws_ecs_cluster_capacity_providers.main,
    aws_iam_role_policy_attachment.task_execution_managed,
  ]

  tags = {
    Name    = "wchats-pipeline-worker"
    Project = "wchats"
  }
}
