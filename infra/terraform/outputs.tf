output "load_balancer_dns" {
  description = "Public DNS name of the load balancer."
  value       = aws_lb.main.dns_name
}

output "api_url" {
  description = "Base URL for the Forge API."
  value       = var.certificate_arn == "" ? "http://${aws_lb.main.dns_name}" : "https://${aws_lb.main.dns_name}"
}

output "ecr_repository_url" {
  description = "ECR repository for build artifacts."
  value       = aws_ecr_repository.artifacts.repository_url
}

output "database_endpoint" {
  description = "RDS endpoint. Reachable only from the task tier."
  value       = aws_db_instance.main.address
}

output "secrets_arn" {
  description = "ARN of the application secret. Values are never exposed as outputs."
  value       = aws_secretsmanager_secret.application.arn
}

output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.main.name
}

output "log_group" {
  description = "CloudWatch log group for all Forge services."
  value       = aws_cloudwatch_log_group.main.name
}
