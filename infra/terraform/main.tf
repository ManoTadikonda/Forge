# Forge on AWS: ECS Fargate behind an ALB, with RDS PostgreSQL and ElastiCache.
#
# STATUS: NEVER APPLIED. This configuration has not been run against a real AWS
# account. It is formatted and validated in CI (`terraform validate`), which checks
# syntax and provider schemas — not that it produces a working deployment.
# Treat it as a reviewed design, not a tested artefact. See docs/deployment.md.

locals {
  name = "${var.project}-${var.environment}"

  common_tags = merge(
    {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      Component   = "forge"
    },
    var.tags,
  )

  # Public subnets host only the load balancer. Everything that runs application
  # code or stores data sits in private subnets with no inbound route.
  public_subnet_cidrs  = [for i in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 8, i)]
  private_subnet_cidrs = [for i in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 8, i + 10)]
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ------------------------------------------------------------------ network

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.common_tags, { Name = local.name })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.common_tags, { Name = local.name })
}

resource "aws_subnet" "public" {
  count = var.availability_zone_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = local.public_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]

  # Tasks never run here, so no public IP is auto-assigned.
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, { Name = "${local.name}-public-${count.index}", Tier = "public" })
}

resource "aws_subnet" "private" {
  count = var.availability_zone_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = local.private_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = merge(local.common_tags, { Name = "${local.name}-private-${count.index}", Tier = "private" })
}

resource "aws_eip" "nat" {
  count  = var.availability_zone_count
  domain = "vpc"
  tags   = merge(local.common_tags, { Name = "${local.name}-nat-${count.index}" })
}

# One NAT gateway per AZ: a single shared gateway is a cross-AZ dependency and a
# single point of failure for all outbound traffic.
resource "aws_nat_gateway" "main" {
  count = var.availability_zone_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  depends_on    = [aws_internet_gateway.main]

  tags = merge(local.common_tags, { Name = "${local.name}-nat-${count.index}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(local.common_tags, { Name = "${local.name}-public" })
}

resource "aws_route_table_association" "public" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count = var.availability_zone_count

  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  tags = merge(local.common_tags, { Name = "${local.name}-private-${count.index}" })
}

resource "aws_route_table_association" "private" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}
