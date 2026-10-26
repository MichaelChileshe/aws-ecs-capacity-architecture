# Two different jobs, two different roles:
#   EXECUTION role: used by ECS/Fargate itself BEFORE and AROUND the app: pull the image, create
#                   the log stream, write logs. The app never sees these credentials.
#   TASK role:      the identity the app's own code gets (container credentials endpoint
#                   169.254.170.2). routes-api and eta-engine call no AWS API at all, so their task
#                   roles have NO permissions: they exist so each service has its own identity and
#                   nothing more. /whoami returns the RoleArn to prove which one the code runs as.

resource "aws_iam_role" "execution" {
  name = "${local.name}-execution"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

locals {
  # Only ECS tasks in THIS account may assume the task roles (confused-deputy protection).
  task_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        ArnLike      = { "aws:SourceArn" = "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:*" }
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

resource "aws_iam_role" "routes_task" {
  name               = "${local.name}-routes-api-task"
  description        = "routes-api application identity: no AWS permissions needed"
  assume_role_policy = local.task_trust
}

resource "aws_iam_role" "eta_task" {
  name               = "${local.name}-eta-engine-task"
  description        = "eta-engine / eta-batch application identity: no AWS permissions needed"
  assume_role_policy = local.task_trust
}
