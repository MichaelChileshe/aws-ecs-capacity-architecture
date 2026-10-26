output "alb_dns" {
  description = "The ALB's DNS name: what dispatchers and the driver app call."
  value       = aws_lb.alb.dns_name
}

output "nlb_dns" {
  description = "The optional NLB's DNS name (null unless nlb = true)."
  value       = one(aws_lb.nlb[*].dns_name)
}

output "cluster" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.main.name
}

output "services" {
  description = "ECS service names."
  value       = { routes = aws_ecs_service.routes.name, eta = aws_ecs_service.eta.name, eta_batch = one(aws_ecs_service.eta_batch[*].name) }
}

output "routes_mode" {
  description = "Current routes-api capacity mode and its strategy."
  value       = { mode = var.routes_mode, desired = var.routes_desired, strategy = local.strategies[var.routes_mode] }
}

output "roles" {
  description = "The execution role (ECS's own use) and the task roles (the apps' identities)."
  value = {
    execution  = aws_iam_role.execution.arn
    routes_api = aws_iam_role.routes_task.arn
    eta_engine = aws_iam_role.eta_task.arn
  }
}

output "loadgen" {
  description = "What scripts/loadgen.py needs for run-task."
  value = {
    task_definition = aws_ecs_task_definition.loadgen.family
    subnets         = aws_subnet.public[*].id
    security_group  = aws_security_group.loadgen.id
    log_group       = aws_cloudwatch_log_group.main.name
  }
}

output "target_groups" {
  description = "Target group ARNs: alb (always), nlb (null unless nlb = true)."
  value       = { alb = aws_lb_target_group.routes.arn, nlb = one(aws_lb_target_group.nlb[*].arn) }
}

output "task_size" {
  description = "Fargate task size of routes-api / eta-engine (app + Service Connect proxy), and of eta-batch on EC2."
  value       = { cpu = local.task_cpu, memory = local.task_memory, ec2_cpu = 256, ec2_memory = 512 }
}

output "ec2" {
  description = "EC2 capacity provider details (null unless ec2_capacity = true)."
  value = var.ec2_capacity ? {
    asg               = aws_autoscaling_group.ecs[0].name
    capacity_provider = aws_ecs_capacity_provider.ec2[0].name
    instance_type     = var.instance_type
    launch_template   = aws_launch_template.ecs[0].name
  } : null
}

output "instance_type" {
  description = "EC2 container instance type (used by the ENI and price scripts even after the EC2 drill)."
  value       = var.instance_type
}

output "namespace" {
  description = "Service Connect (Cloud Map HTTP) namespace."
  value       = { name = aws_service_discovery_http_namespace.main.name, id = aws_service_discovery_http_namespace.main.id }
}

output "azs" {
  description = "The two AZs the subnets are in."
  value       = local.azs
}

output "app_sha256" {
  description = "SHA-256 (first 16) of the app files baked into the task definitions."
  value = {
    routes_api = substr(filesha256("${path.module}/../app/routes_api.py"), 0, 16)
    eta_engine = substr(filesha256("${path.module}/../app/eta_engine.py"), 0, 16)
    loadgen    = substr(filesha256("${path.module}/../app/loadgen.py"), 0, 16)
  }
}
