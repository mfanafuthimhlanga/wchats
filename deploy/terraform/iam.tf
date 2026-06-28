# ---------------------------------------------------------------------------
# IAM — Least-privilege roles for ECS Fargate tasks (T-13-01-03)
#
# Role A: task_execution — ECS infrastructure role
#   • Managed: AmazonECSTaskExecutionRolePolicy (ECR pull + CloudWatch logs)
#   • Inline: secretsmanager:GetSecretValue on specific secret ARNs only
#   • Inline: ecr:GetAuthorizationToken on * (AWS requires * for this action)
#
# Role B: task — Application-level role
#   • bedrock:InvokeModel scoped to amazon.titan-embed-text-v2:0 ONLY (not *)
#   • s3:GetObject/PutObject/DeleteObject scoped to uploads bucket ARN only
#   • secretsmanager:GetSecretValue on specific secret ARNs only
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Role A: Task Execution Role
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "wchats-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json

  tags = {
    Name    = "wchats-task-execution-role"
    Project = "wchats"
  }
}

# AWS-managed policy covering ECR image pull and CloudWatch log stream creation
resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Inline: Secrets Manager read + ECR token (ecr:GetAuthorizationToken requires *)
resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "wchats-task-execution-secrets"
  role = aws_iam_role.task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SecretsManagerRead"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = local.all_secret_arns
      },
      {
        Sid    = "ECRAuthToken"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        # GetAuthorizationToken is an account-level API — AWS requires Resource "*"
        Resource = ["*"]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Role B: Task Role (application permissions)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "task" {
  name               = "wchats-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json

  tags = {
    Name    = "wchats-task-role"
    Project = "wchats"
  }
}

resource "aws_iam_role_policy" "task" {
  name = "wchats-task-policy"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # T-13-01-03: bedrock:InvokeModel scoped to Titan v2 ARN ONLY — no wildcard resource
        Sid    = "BedrockEmbedding"
        Effect = "Allow"
        Action = "bedrock:InvokeModel"
        Resource = [
          "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0",
        ]
      },
      {
        # T-13-01-03: S3 object ops scoped to uploads bucket ARN only — no wildcard resource
        Sid    = "S3Uploads"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = [
          aws_s3_bucket.uploads.arn,
          "${aws_s3_bucket.uploads.arn}/*",
        ]
      },
      {
        Sid      = "SecretsManagerRead"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = local.all_secret_arns
      },
    ]
  })
}
