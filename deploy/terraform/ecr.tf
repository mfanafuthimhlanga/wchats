# ---------------------------------------------------------------------------
# ECR Repositories
# Both images are built with `docker buildx build --platform linux/amd64`
# and pushed to these repos before `terraform apply` in 13-08.
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "api" {
  name                 = "wchats-api"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name    = "wchats-api"
    Project = "wchats"
  }
}

resource "aws_ecr_repository" "pipeline" {
  name                 = "wchats-pipeline"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name    = "wchats-pipeline"
    Project = "wchats"
  }
}
