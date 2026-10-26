# ---- EC2 drill: an Auto Scaling group capacity provider (Graviton t4g, ECS-optimized AL2023) ----
# The ASG starts EMPTY (desired 0). ECS managed scaling grows it when tasks need a place to run
# and shrinks it when they're gone; Terraform never touches desired_capacity after creation.
# eta-batch (the scenario's nightly route recomputation workers) runs on it.

data "aws_ssm_parameter" "ecs_ami" {
  count = var.ec2_capacity ? 1 : 0
  name  = "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id"
}

resource "aws_iam_role" "instance" {
  count = var.ec2_capacity ? 1 : 0
  name  = "${local.name}-ecs-instance"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "instance_ecs" {
  count      = var.ec2_capacity ? 1 : 0
  role       = aws_iam_role.instance[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "instance_ssm" {
  count      = var.ec2_capacity ? 1 : 0
  role       = aws_iam_role.instance[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  count = var.ec2_capacity ? 1 : 0
  name  = "${local.name}-ecs-instance"
  role  = aws_iam_role.instance[0].name
}

# Instances: nothing in (commands go through SSM), everything out (ECS, SSM, image pulls, logs).
resource "aws_security_group" "instances" {
  count       = var.ec2_capacity ? 1 : 0
  name        = "${local.name}-instances"
  description = "ECS container instances: no inbound"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_egress_rule" "instances_out" {
  count             = var.ec2_capacity ? 1 : 0
  security_group_id = aws_security_group.instances[0].id
  description       = "ECS and SSM endpoints, image pulls, CloudWatch Logs"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_launch_template" "ecs" {
  count         = var.ec2_capacity ? 1 : 0
  name          = "${local.name}-ecs"
  image_id      = data.aws_ssm_parameter.ecs_ami[0].insecure_value
  instance_type = var.instance_type
  user_data     = base64encode(templatefile("${path.module}/templates/userdata.sh.tftpl", { cluster = aws_ecs_cluster.main.name }))

  iam_instance_profile {
    arn = aws_iam_instance_profile.instance[0].arn
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1
  }

  credit_specification {
    cpu_credits = "standard" # no surprise "unlimited" surplus-credit charges
  }

  network_interfaces {
    device_index                = 0
    associate_public_ip_address = true
    security_groups             = [aws_security_group.instances[0].id]
    delete_on_termination       = true
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = 30
      encrypted             = true
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.name}-ecs", Project = "aws-ecs-capacity-architecture" }
  }

  tag_specifications {
    resource_type = "volume"
    tags          = { Name = "${local.name}-ecs", Project = "aws-ecs-capacity-architecture" }
  }
}

resource "aws_autoscaling_group" "ecs" {
  count                 = var.ec2_capacity ? 1 : 0
  name                  = "${local.name}-ecs"
  min_size              = 0
  max_size              = 4
  desired_capacity      = 0
  vpc_zone_identifier   = aws_subnet.public[*].id
  health_check_type     = "EC2"
  force_delete          = true
  protect_from_scale_in = false

  launch_template {
    id      = aws_launch_template.ecs[0].id
    version = aws_launch_template.ecs[0].latest_version
  }

  # ECS adds this tag when the capacity provider is associated; declaring it keeps Terraform from
  # removing it on the next apply (without it, ECS can't manage the group's scaling).
  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }

  tag {
    key                 = "Name"
    value               = "${local.name}-ecs"
    propagate_at_launch = true
  }

  # The instances need their IAM policies AND their outbound rule for their whole life, including
  # while ECS drains them at removal. Without this, Terraform deletes the attachments and the
  # egress rule first (nothing depends on them), the ECS agents lose permissions and their route
  # to ECS, and the service can never finish draining (it happened: 20-minute timeout).
  depends_on = [aws_iam_role_policy_attachment.instance_ecs, aws_iam_role_policy_attachment.instance_ssm, aws_vpc_security_group_egress_rule.instances_out]

  lifecycle {
    ignore_changes = [desired_capacity] # owned by ECS managed scaling
  }
}

resource "aws_ecs_capacity_provider" "ec2" {
  count = var.ec2_capacity ? 1 : 0
  name  = "${local.name}-ec2"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.ecs[0].arn
    managed_termination_protection = "DISABLED" # the lab ASG must be deletable at any time
    managed_draining               = "ENABLED"

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 100 # fill instances completely before adding another
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 4
      instance_warmup_period    = 60
    }
  }

  # AWS refuses to delete a capacity provider that is still associated with the cluster.
  # This makes Terraform update the cluster's capacity provider list first, then delete it.
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_ecs_task_definition" "eta_batch" {
  count                    = var.ec2_capacity ? 1 : 0
  family                   = "${local.name}-eta-batch"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc" # each task gets its OWN network interface on the instance
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.eta_task.arn

  container_definitions = jsonencode([{
    name                   = "eta-batch"
    image                  = var.image # multi-arch: the arm64 variant is pulled on Graviton
    essential              = true
    user                   = "65534"
    readonlyRootFilesystem = true
    command                = ["python3", "-u", "-c", format(local.run_b64, "APP_B64", "eta_engine.py")]
    stopTimeout            = 30
    environment = [
      { name = "APP_B64", value = filebase64("${path.module}/../app/eta_engine.py") },
      { name = "SERVICE_NAME", value = "eta-batch" },
      { name = "PORT", value = "8080" },
    ]
    portMappings     = [{ containerPort = 8080, protocol = "tcp" }]
    logConfiguration = { logDriver = "awslogs", options = merge(local.log_options, { "awslogs-stream-prefix" = "eta-batch" }) }
  }])
}

resource "aws_ecs_service" "eta_batch" {
  count                = var.ec2_capacity ? 1 : 0
  name                 = "eta-batch"
  cluster              = aws_ecs_cluster.main.id
  task_definition      = aws_ecs_task_definition.eta_batch[0].arn
  desired_count        = var.ec2_tasks
  force_new_deployment = true
  propagate_tags       = "SERVICE"

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.ec2[0].name
    base              = 0
    weight            = 1
  }

  # First spread across AZs, then pack each instance by memory before using another one.
  ordered_placement_strategy {
    type  = "spread"
    field = "attribute:ecs.availability-zone"
  }

  ordered_placement_strategy {
    type  = "binpack"
    field = "memory"
  }

  network_configuration {
    subnets         = aws_subnet.public[*].id
    security_groups = [aws_security_group.tasks.id]
    # assign_public_ip isn't supported for EC2 tasks; this one needs no internet (the instance
    # pulls the image and ships the logs on its own network interface).
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_ecs_cluster_capacity_providers.main]
}
