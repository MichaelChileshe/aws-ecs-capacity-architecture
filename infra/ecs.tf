# ---- cluster, capacity providers, Service Connect namespace, logs ----

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled" # the drills measure with their own scripts; Container Insights bills per metric
  }
}

# FARGATE and FARGATE_SPOT always; the EC2 Auto Scaling group capacity provider only in the EC2 drill.
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = concat(["FARGATE", "FARGATE_SPOT"], aws_ecs_capacity_provider.ec2[*].name)

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 0
  }
}

# Service Connect: a Cloud Map namespace (HTTP: API discovery only, no Route 53 private zone).
resource "aws_service_discovery_http_namespace" "main" {
  name        = local.name
  description = "Service Connect namespace for the Mvelaphanda route services"
}

resource "aws_cloudwatch_log_group" "main" {
  name              = "/ecs/${local.name}"
  retention_in_days = 1
}

locals {
  # The container command: decode the app from an environment variable and run it. A stock python
  # image + this one line = no Dockerfile, no registry, no image build.
  run_b64 = "import base64,os;exec(compile(base64.b64decode(os.environ['%s']),'%s','exec'))"

  # Each Fargate task: 0.25 vCPU for the app + 0.25 vCPU for the Service Connect proxy (AWS
  # recommends adding 256 CPU units and at least 64 MiB for it).
  task_cpu    = 512
  task_memory = 1024

  # routes-api capacity provider strategy per phase.
  strategies = {
    fargate = [{ cp = "FARGATE", base = 0, weight = 1 }]
    spot    = [{ cp = "FARGATE_SPOT", base = 0, weight = 1 }]
    mixed = [
      { cp = "FARGATE", base = var.mixed_base, weight = var.mixed_weights.fargate },
      { cp = "FARGATE_SPOT", base = 0, weight = var.mixed_weights.spot },
    ]
  }

  log_options = {
    "awslogs-group"  = aws_cloudwatch_log_group.main.name
    "awslogs-region" = var.region
  }
}

# ---- task definitions ----

resource "aws_ecs_task_definition" "routes" {
  family                   = "${local.name}-routes-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = local.task_cpu
  memory                   = local.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.routes_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name                   = "routes-api"
    image                  = var.image
    essential              = true
    user                   = "65534" # nobody: the app needs no root
    readonlyRootFilesystem = true
    command                = ["python3", "-u", "-c", format(local.run_b64, "APP_B64", "routes_api.py")]
    stopTimeout            = 30 # SIGTERM -> up to 30 s to finish -> SIGKILL (Fargate allows up to 120)
    environment = [
      { name = "APP_B64", value = filebase64("${path.module}/../app/routes_api.py") },
      { name = "SERVICE_NAME", value = "routes-api" },
      { name = "ETA_URL", value = "http://eta:8080" },
      { name = "PORT", value = "8080" },
      { name = "DRAIN_SECONDS", value = "5" },
    ]
    portMappings     = [{ name = "http", containerPort = 8080, protocol = "tcp", appProtocol = "http" }]
    logConfiguration = { logDriver = "awslogs", options = merge(local.log_options, { "awslogs-stream-prefix" = "routes-api" }) }
  }])
}

resource "aws_ecs_task_definition" "eta" {
  family                   = "${local.name}-eta-engine"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = local.task_cpu
  memory                   = local.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.eta_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name                   = "eta-engine"
    image                  = var.image
    essential              = true
    user                   = "65534"
    readonlyRootFilesystem = true
    command                = ["python3", "-u", "-c", format(local.run_b64, "APP_B64", "eta_engine.py")]
    stopTimeout            = 30
    environment = [
      { name = "APP_B64", value = filebase64("${path.module}/../app/eta_engine.py") },
      { name = "SERVICE_NAME", value = "eta-engine" },
      { name = "PORT", value = "8080" },
      { name = "DRAIN_SECONDS", value = "5" },
    ]
    portMappings     = [{ name = "http", containerPort = 8080, protocol = "tcp", appProtocol = "http" }]
    logConfiguration = { logDriver = "awslogs", options = merge(local.log_options, { "awslogs-stream-prefix" = "eta-engine" }) }
  }])
}

# The load generator: a one-off task started by scripts/loadgen.py with run-task (no service).
# No task role: it calls no AWS API, it only sends HTTP requests to the ALB.
resource "aws_ecs_task_definition" "loadgen" {
  family                   = "${local.name}-loadgen"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name                   = "loadgen"
    image                  = var.image
    essential              = true
    user                   = "65534"
    readonlyRootFilesystem = true
    command                = ["python3", "-u", "-c", format(local.run_b64, "LOADGEN_B64", "loadgen.py")]
    stopTimeout            = 10
    environment = [
      { name = "LOADGEN_B64", value = filebase64("${path.module}/../app/loadgen.py") },
      { name = "TARGET_URL", value = "http://${aws_lb.alb.dns_name}" },
      { name = "PATH_QS", value = "/route?from=JNB&to=DBN" },
      { name = "PROFILE", value = "20:60" },
      { name = "TIMEOUT", value = "3" },
    ]
    logConfiguration = { logDriver = "awslogs", options = merge(local.log_options, { "awslogs-stream-prefix" = "loadgen" }) }
  }])
}

# ---- services ----

# eta-engine: internal only. Service Connect "client-server": it publishes the endpoint eta:8080.
resource "aws_ecs_service" "eta" {
  name                               = "eta-engine"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.eta.arn
  desired_count                      = var.eta_desired
  force_new_deployment               = true
  propagate_tags                     = "SERVICE"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    base              = 0
    weight            = 1
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn

    service {
      port_name      = "http"
      discovery_name = "eta"
      client_alias {
        port     = 8080
        dns_name = "eta"
      }
    }
  }

  depends_on = [aws_ecs_cluster_capacity_providers.main, aws_iam_role_policy_attachment.execution]
}

# routes-api: behind the ALB (and optionally the NLB). Service Connect "client": it can call eta:8080.
resource "aws_ecs_service" "routes" {
  name                               = "routes-api"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.routes.arn
  desired_count                      = var.routes_desired
  force_new_deployment               = true # required to change the capacity provider strategy in place
  propagate_tags                     = "SERVICE"
  health_check_grace_period_seconds  = 10
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  dynamic "capacity_provider_strategy" {
    for_each = local.strategies[var.routes_mode]
    content {
      capacity_provider = capacity_provider_strategy.value.cp
      base              = capacity_provider_strategy.value.base
      weight            = capacity_provider_strategy.value.weight
    }
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.routes.arn
    container_name   = "routes-api"
    container_port   = 8080
  }

  dynamic "load_balancer" {
    for_each = aws_lb_target_group.nlb[*].arn
    content {
      target_group_arn = load_balancer.value
      container_name   = "routes-api"
      container_port   = 8080
    }
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn
  }

  # eta-engine first: a Service Connect client only learns the endpoints that exist when its
  # tasks start.
  depends_on = [aws_lb_listener.http, aws_lb_listener.nlb, aws_ecs_service.eta, aws_ecs_cluster_capacity_providers.main]
}
