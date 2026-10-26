# ---- Optional drill: a Network Load Balancer (TCP 80) in front of the SAME routes-api service ----
# A second target group on the service. The NLB passes TCP through: no HTTP routing, no
# X-Forwarded-For; with client IP preservation off (set below) the task sees the NLB's own
# private address. It gets one static address per AZ.

resource "aws_security_group" "nlb" {
  count       = var.nlb ? 1 : 0
  name        = "${local.name}-nlb"
  description = "routes-api NLB: TCP 80 from the internet, out to the tasks only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "nlb_tcp80" {
  count             = var.nlb ? 1 : 0
  security_group_id = aws_security_group.nlb[0].id
  description       = "TCP 80 from the internet"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "nlb_to_tasks" {
  count                        = var.nlb ? 1 : 0
  security_group_id            = aws_security_group.nlb[0].id
  description                  = "to routes-api tasks (traffic and health checks)"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.tasks.id
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_nlb" {
  count                        = var.nlb ? 1 : 0
  security_group_id            = aws_security_group.tasks.id
  description                  = "from the NLB"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.nlb[0].id
}

resource "aws_lb" "nlb" {
  count              = var.nlb ? 1 : 0
  name               = "${local.name}-nlb"
  load_balancer_type = "network"
  internal           = false
  security_groups    = [aws_security_group.nlb[0].id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "nlb" {
  count                = var.nlb ? 1 : 0
  name                 = "${local.name}-nlb-tg"
  port                 = 8080
  protocol             = "TCP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 15
  preserve_client_ip   = "false"

  health_check {
    protocol            = "HTTP"
    path                = "/health"
    matcher             = "200"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

resource "aws_lb_listener" "nlb" {
  count             = var.nlb ? 1 : 0
  load_balancer_arn = aws_lb.nlb[0].arn
  port              = 80
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.nlb[0].arn
  }
}
