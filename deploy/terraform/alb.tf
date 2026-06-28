# ---------------------------------------------------------------------------
# Application Load Balancer
# T-13-01-02: idle_timeout=4000 (fixes SSE cut at ALB default 60s — Landmine 2)
# HTTP:80 → HTTPS:443 301 redirect (T-13-01-02 plaintext prevention)
# /health target group (PROD-04 ALB health supervision)
# ---------------------------------------------------------------------------

# ALB security group — internet-facing; egress all (avoids circular dep with ecs_tasks SG)
resource "aws_security_group" "alb" {
  name        = "wchats-alb-sg"
  description = "ALB: allow HTTP/HTTPS from internet; allow all outbound to Fargate tasks"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from internet (redirects to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS from internet (ACM TLS termination)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound (routes to Fargate task private subnets)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "wchats-alb-sg"
    Project = "wchats"
  }
}

# Application Load Balancer (internet-facing; TLS termination at ALB)
resource "aws_lb" "main" {
  name               = "wchats-alb"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = [aws_subnet.public_a.id, aws_subnet.public_b.id]

  # T-13-01-02 / Landmine 2: ALB default idle_timeout=60s severs SSE streams.
  # SSE hard cap is 120s (widget.py asyncio.timeout(120)); keepalive fires every 3s.
  # 4000s is the ALB maximum; any future keepalive gap is safe.
  idle_timeout = 4000

  enable_deletion_protection = false

  tags = {
    Name    = "wchats-alb"
    Project = "wchats"
  }
}

# Target group for the API service (PROD-04 health supervision)
resource "aws_lb_target_group" "api" {
  name        = "wchats-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/health"
    protocol            = "HTTP"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = {
    Name    = "wchats-api-tg"
    Project = "wchats"
  }
}

# HTTPS:443 listener — ACM cert; forwards to API target group
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# HTTP:80 listener — 301 redirect to HTTPS (T-13-01-02)
resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
