# ---- Service auto scaling for routes-api (Application Auto Scaling, target tracking) ----
# Target: var.scale_target requests per task per minute, measured by the ALB for the routes-api
# target group. Application Auto Scaling creates and owns the CloudWatch alarms.

resource "aws_appautoscaling_target" "routes" {
  count              = var.autoscaling ? 1 : 0
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.routes.name}"
  min_capacity       = var.scale_min
  max_capacity       = var.scale_max
}

resource "aws_appautoscaling_policy" "routes" {
  count              = var.autoscaling ? 1 : 0
  name               = "${local.name}-requests-per-task"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.routes[0].service_namespace
  scalable_dimension = aws_appautoscaling_target.routes[0].scalable_dimension
  resource_id        = aws_appautoscaling_target.routes[0].resource_id

  target_tracking_scaling_policy_configuration {
    target_value       = var.scale_target
    scale_out_cooldown = 30
    scale_in_cooldown  = 60 # short for the lab; production would keep the default 300

    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.alb.arn_suffix}/${aws_lb_target_group.routes.arn_suffix}"
    }
  }
}
