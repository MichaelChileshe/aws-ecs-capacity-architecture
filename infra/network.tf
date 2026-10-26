# Mvelaphanda Logistics route-optimisation platform on Amazon ECS.
# One VPC, two public subnets in two AZs, no NAT gateway: Fargate tasks get a public IP to pull
# the image and ship logs; EC2 container instances get a public IP for the same reason. Nothing
# accepts traffic from the internet except the load balancer(s).

locals {
  name = "mvelaphanda-routes"
  azs  = length(var.az_names) == 2 ? var.az_names : slice(data.aws_availability_zones.available.names, 0, 2)
}

data "aws_availability_zones" "available" {
  state = "available"
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

data "aws_caller_identity" "current" {}

resource "aws_vpc" "main" {
  cidr_block           = "10.160.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public-${count.index}" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-public" }
}

resource "aws_route" "internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---- security groups ----
# ALB: port 80 from anywhere in, and out only to the tasks on 8080.
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "routes-api ALB: HTTP 80 from the internet, out to the tasks only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from dispatchers and the driver app"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  description                  = "to routes-api tasks"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.tasks.id
}

# Tasks: 8080 only from the ALB and from each other (Service Connect: routes-api's proxy talks to
# eta-engine's proxy on the container port). Out: anywhere (image pull, logs, ECS endpoints).
resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks"
  description = "ECS tasks: 8080 from the ALB and from each other only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "from the ALB"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_tasks" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Service Connect between tasks"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.tasks.id
}

resource "aws_vpc_security_group_egress_rule" "tasks_out" {
  security_group_id = aws_security_group.tasks.id
  description       = "image pull, CloudWatch Logs, ECS and Service Connect endpoints"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# Load generator: nothing in at all, out to the ALB (and the image/log endpoints).
resource "aws_security_group" "loadgen" {
  name        = "${local.name}-loadgen"
  description = "one-off load generator task: no inbound"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_egress_rule" "loadgen_out" {
  security_group_id = aws_security_group.loadgen.id
  description       = "to the ALB, image pull, CloudWatch Logs"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
