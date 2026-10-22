# ECS Fargate services behind an Application Load Balancer.

resource "aws_cloudwatch_log_group" "main" {
  name              = "/aws/ecs/${local.name}"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = var.environment == "production" ? "enabled" : "disabled"
  }

  tags = local.common_tags
}

resource "aws_lb" "main" {
  name_prefix        = substr(var.project, 0, 6)
  load_balancer_type = "application"
  internal           = false
  subnets            = aws_subnet.public[*].id
  security_groups    = [aws_security_group.alb.id]

  drop_invalid_header_fields = true
  enable_deletion_protection = var.environment == "production"
  idle_timeout               = 60

  tags = local.common_tags
}

resource "aws_lb_target_group" "api" {
  name_prefix = substr(var.project, 0, 6)
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled = true
    # /ready, not /health: readiness checks the database, so a task that cannot
    # serve is drained rather than left in rotation.
    path                = "/ready"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
    matcher             = "200"
  }

  deregistration_delay = 30

  tags = local.common_tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  # With a certificate, HTTP only redirects. Without one, it serves directly —
  # which is why certificate_arn should always be set outside a sandbox.
  dynamic "default_action" {
    for_each = var.certificate_arn == "" ? [1] : []
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.api.arn
    }
  }

  dynamic "default_action" {
    for_each = var.certificate_arn == "" ? [] : [1]
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

resource "aws_lb_listener" "https" {
  count = var.certificate_arn == "" ? 0 : 1

  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

locals {
  # Non-secret configuration. Every secret is injected via `secrets` instead, so
  # it never appears in the task definition.
  common_environment = [
    { name = "FORGE_ENVIRONMENT", value = var.environment == "dev" ? "staging" : var.environment },
    { name = "FORGE_REDIS_URL", value = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0" },
    { name = "FORGE_LOG_FORMAT", value = "json" },
    { name = "FORGE_REGISTRY", value = "ecr" },
    { name = "FORGE_ECR_REGISTRY", value = aws_ecr_repository.artifacts.repository_url },
    { name = "AWS_REGION", value = var.region },
  ]

  common_secrets = [
    {
      name      = "FORGE_SECRET_KEY"
      valueFrom = "${aws_secretsmanager_secret.application.arn}:FORGE_SECRET_KEY::"
    },
    {
      name      = "FORGE_DATABASE_URL"
      valueFrom = "${aws_secretsmanager_secret.application.arn}:FORGE_DATABASE_URL::"
    },
    {
      name      = "FORGE_GITHUB_WEBHOOK_SECRET"
      valueFrom = "${aws_secretsmanager_secret.application.arn}:FORGE_GITHUB_WEBHOOK_SECRET::"
    },
  ]
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = var.api_image
      essential = true

      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      command      = [
        "python", "-m", "uvicorn", "forge_api.main:app",
        "--host", "0.0.0.0", "--port", "8000",
      ]

      environment = local.common_environment
      secrets     = local.common_secrets

      # Least privilege inside the container as well as around it.
      readonlyRootFilesystem = true
      user                   = "10001:10001"
      linuxParameters        = {
        initProcessEnabled = true
        tmpfs              = [{ containerPath = "/tmp", size = 64, mountOptions = ["noexec", "nosuid"] }]
      }

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      logConfiguration = {
        logDriver = "awslogs"
        options   = {
          "awslogs-group"         = aws_cloudwatch_log_group.main.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "api"
        }
      }
    },
  ])

  tags = local.common_tags
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name        = "worker"
      image       = var.api_image
      essential   = true
      command     = ["python", "-m", "forge_worker.runtime"]
      environment = local.common_environment
      secrets     = local.common_secrets
      user        = "10001:10001"

      logConfiguration = {
        logDriver = "awslogs"
        options   = {
          "awslogs-group"         = aws_cloudwatch_log_group.main.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "worker"
        }
      }
    },
  ])

  tags = local.common_tags
}

resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  # ECS rolling deployment with circuit breaker: a release whose tasks fail to
  # become healthy is rolled back automatically. This is ECS protecting the
  # control plane itself, and is separate from Forge's own canary rollbacks,
  # which protect the applications Forge deploys.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100
  health_check_grace_period_seconds  = 60
  enable_execute_command             = var.environment != "production"

  depends_on = [aws_lb_listener.http]

  tags = local.common_tags

  lifecycle {
    # The deploy pipeline updates the task definition; do not revert it here.
    ignore_changes = [task_definition, desired_count]
  }
}

resource "aws_ecs_service" "worker" {
  name            = "${local.name}-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.common_tags

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}

# ------------------------------------------------------------------- alarms

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name}-api-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "Forge API is returning server errors."
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.api.arn_suffix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  alarm_name          = "${local.name}-unhealthy-hosts"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Average"
  threshold           = 0
  alarm_description   = "One or more Forge API tasks are failing their readiness check."
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.api.arn_suffix
  }

  tags = local.common_tags
}
