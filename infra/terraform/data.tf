# Managed data stores.

resource "aws_db_subnet_group" "main" {
  name_prefix = "${local.name}-"
  subnet_ids  = aws_subnet.private[*].id
  tags        = merge(local.common_tags, { Name = local.name })
}

resource "aws_db_instance" "main" {
  identifier_prefix = "${local.name}-"
  engine            = "postgres"
  engine_version    = "16.6"
  instance_class    = var.database_instance_class

  allocated_storage     = var.database_allocated_storage
  max_allocated_storage = var.database_allocated_storage * 4
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "forge"
  username = "forge"
  password = random_password.database.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  # No public endpoint: the database is reachable only from the task tier.
  publicly_accessible = false

  multi_az                = var.environment == "production"
  backup_retention_period = var.database_backup_retention_days
  backup_window           = "02:00-03:00"
  maintenance_window      = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot   = true

  # Protect production from an accidental `terraform destroy`.
  deletion_protection       = var.environment == "production"
  skip_final_snapshot       = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "${local.name}-final" : null

  auto_minor_version_upgrade      = true
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  performance_insights_enabled    = var.environment == "production"

  tags = merge(local.common_tags, { Name = local.name })

  lifecycle {
    # The password is rotated through Secrets Manager, not by reapplying.
    ignore_changes = [password]
  }
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${local.name}-cache"
  subnet_ids = aws_subnet.private[*].id
  tags       = local.common_tags
}

# Redis holds rate-limit counters and cached values only. Durable state lives in
# PostgreSQL, so losing this cluster costs throughput, not correctness.
resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${local.name}-cache"
  description          = "Forge ephemeral coordination and rate limiting"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = "cache.t4g.micro"
  port           = 6379

  num_cache_clusters         = var.environment == "production" ? 2 : 1
  automatic_failover_enabled = var.environment == "production"
  multi_az_enabled           = var.environment == "production"

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.cache.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  snapshot_retention_limit = var.environment == "production" ? 3 : 0
  apply_immediately        = var.environment != "production"

  tags = local.common_tags
}

resource "aws_ecr_repository" "artifacts" {
  name                 = "${local.name}/artifacts"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "artifacts" {
  repository = aws_ecr_repository.artifacts.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the 50 most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 50
        }
        action = { type = "expire" }
      },
    ]
  })
}
