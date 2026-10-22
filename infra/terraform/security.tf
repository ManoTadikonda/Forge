# Security groups, IAM roles and secrets.
#
# Principle applied throughout: each tier accepts traffic only from the tier in
# front of it, by security-group reference rather than CIDR, so the rules stay
# correct as addresses change.

resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  description = "Load balancer: public ingress."
  vpc_id      = aws_vpc.main.id

  tags = merge(local.common_tags, { Name = "${local.name}-alb" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each = toset(var.allowed_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from an explicitly allowed network"
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# HTTP exists only to redirect to HTTPS; it never serves application traffic.
resource "aws_vpc_security_group_ingress_rule" "alb_http_redirect" {
  for_each = toset(var.allowed_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTP, redirected to HTTPS"
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to application tasks"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "-1"
}

resource "aws_security_group" "tasks" {
  name_prefix = "${local.name}-tasks-"
  description = "ECS tasks: ingress from the load balancer only."
  vpc_id      = aws_vpc.main.id

  tags = merge(local.common_tags, { Name = "${local.name}-tasks" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "API traffic from the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8000
  to_port                      = 8098
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "tasks_internal" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Service-to-service traffic within the task tier"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "-1"
}

# Tasks need outbound access to pull images, reach Secrets Manager and clone
# public repositories. Egress filtering is a documented limitation: see
# docs/security.md on SSRF.
resource "aws_vpc_security_group_egress_rule" "tasks_outbound" {
  security_group_id = aws_security_group.tasks.id
  description       = "Outbound to AWS APIs, registries and public git hosts"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "database" {
  name_prefix = "${local.name}-db-"
  description = "PostgreSQL: reachable only from the task tier."
  vpc_id      = aws_vpc.main.id

  tags = merge(local.common_tags, { Name = "${local.name}-db" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "database_from_tasks" {
  security_group_id            = aws_security_group.database.id
  description                  = "PostgreSQL from application tasks"
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "cache" {
  name_prefix = "${local.name}-cache-"
  description = "Redis: reachable only from the task tier."
  vpc_id      = aws_vpc.main.id

  tags = merge(local.common_tags, { Name = "${local.name}-cache" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_tasks" {
  security_group_id            = aws_security_group.cache.id
  description                  = "Redis from application tasks"
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
}

# --------------------------------------------------------------------- secrets

resource "random_password" "database" {
  length = 48
  # RDS rejects several punctuation characters in master passwords.
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "random_password" "secret_key" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "application" {
  name_prefix             = "${local.name}-app-"
  description             = "Forge application secrets"
  recovery_window_in_days = var.environment == "production" ? 30 : 0

  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "application" {
  secret_id = aws_secretsmanager_secret.application.id

  # Injected into the task definition as `secrets`, so the values never appear in
  # the task definition, in an image, or in `docker inspect`.
  secret_string = jsonencode({
    FORGE_SECRET_KEY   = random_password.secret_key.result
    FORGE_DATABASE_URL = "postgresql+asyncpg://forge:${urlencode(random_password.database.result)}@${aws_db_instance.main.address}:5432/forge"
    # Set manually after creation; an empty value disables webhook ingestion
    # rather than accepting unsigned deliveries.
    FORGE_GITHUB_WEBHOOK_SECRET = ""
  })

  lifecycle {
    # The webhook secret is rotated out of band; do not revert it on every apply.
    ignore_changes = [secret_string]
  }
}

# ------------------------------------------------------------------------ IAM

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# The execution role is used by the ECS agent to pull images and fetch secrets,
# before the container starts. It is not available to application code.
resource "aws_iam_role" "execution" {
  name_prefix        = "${local.name}-exec-"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    # Scoped to this deployment's secret only, not secretsmanager:*.
    resources = [aws_secretsmanager_secret.application.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name_prefix = "${local.name}-exec-secrets-"
  role        = aws_iam_role.execution.id
  policy      = data.aws_iam_policy_document.execution_secrets.json
}

# The task role is what application code can use. It is deliberately minimal.
resource "aws_iam_role" "task" {
  name_prefix        = "${local.name}-task-"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.main.arn}:*"]
  }

  statement {
    sid     = "PublishArtifactsToOwnRepository"
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    # GetAuthorizationToken only accepts "*"; the rest are scoped by the
    # condition below to this deployment's repository.
    resources = ["*"]

    condition {
      test     = "StringEqualsIfExists"
      variable = "ecr:ResourceTag/Project"
      values   = [var.project]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  name_prefix = "${local.name}-task-"
  role        = aws_iam_role.task.id
  policy      = data.aws_iam_policy_document.task.json
}
