variable "project" {
  description = "Name prefix applied to every resource."
  type        = string
  default     = "forge"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project))
    error_message = "project must be lowercase alphanumeric with hyphens, 2-21 characters."
  }
}

variable "environment" {
  description = "Deployment environment."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be one of: dev, staging, production."
  }
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "eu-west-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "availability_zone_count" {
  description = "Number of availability zones. Two is the minimum for a highly available ALB and RDS."
  type        = number
  default     = 2

  validation {
    condition     = var.availability_zone_count >= 2
    error_message = "At least two availability zones are required for the ALB and RDS."
  }
}

variable "api_image" {
  description = "Image for the API and worker, pinned by digest. A mutable tag is refused: deployments must be reproducible."
  type        = string

  validation {
    condition     = can(regex("@sha256:[a-f0-9]{64}$", var.api_image))
    error_message = "api_image must be pinned by digest, for example repo/name@sha256:<64 hex chars>."
  }
}

variable "router_image" {
  description = "Image for the routing proxy, pinned by digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[a-f0-9]{64}$", var.router_image))
    error_message = "router_image must be pinned by digest."
  }
}

variable "api_desired_count" {
  description = "Number of API tasks."
  type        = number
  default     = 2
}

variable "worker_desired_count" {
  description = "Number of worker tasks."
  type        = number
  default     = 1
}

variable "api_cpu" {
  description = "API task CPU units (1024 = 1 vCPU)."
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "API task memory in MiB."
  type        = number
  default     = 1024
}

variable "database_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "database_allocated_storage" {
  description = "RDS allocated storage in GiB."
  type        = number
  default     = 20
}

variable "database_backup_retention_days" {
  description = "Automated backup retention. Production should not be below 7."
  type        = number
  default     = 7
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}

variable "allowed_ingress_cidrs" {
  description = "CIDRs permitted to reach the load balancer. Defaults to nothing: open it deliberately."
  type        = list(string)
  default     = []
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS. When empty, only an HTTP listener is created, which is unsuitable for production."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags applied to every resource."
  type        = map(string)
  default     = {}
}
