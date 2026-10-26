# ---- Application Load Balancer: HTTP 80 -> routes-api (ip targets: each task's own ENI) ----

resource "aws_lb" "alb" {
  name                       = "${local.name}-alb"
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
  idle_timeout               = 30
}

resource "aws_lb_target_group" "routes" {
  name                 = "${local.name}-api"
  port                 = 8080
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 15 # drills: a stopping task leaves the target group after 15 s, not 300

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 5
    timeout             = 3
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.alb.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.routes.arn
  }
}

# Layer 7 at the edge: the admin paths never reach a task. (The NLB can't do this: it sees TCP.)
resource "aws_lb_listener_rule" "block_admin" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  condition {
    path_pattern {
      values = ["/admin", "/admin/*"]
    }
  }

  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "blocked at the load balancer"
      status_code  = "403"
    }
  }
}
