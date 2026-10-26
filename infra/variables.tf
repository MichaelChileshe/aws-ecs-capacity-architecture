variable "region" {
  description = "Region for everything in this project."
  type        = string
  default     = "us-east-1"
}

variable "az_names" {
  description = "Two Availability Zone names for the subnets. Empty = the first two available AZs."
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.az_names) == 0 || length(var.az_names) == 2
    error_message = "az_names must be empty or exactly two AZ names."
  }
}

variable "image" {
  description = "Public multi-arch (amd64 + arm64) Python image. No image build: the app code arrives base64-encoded in an environment variable."
  type        = string
  default     = "public.ecr.aws/docker/library/python:3.12-slim"
}

variable "instance_type" {
  description = "EC2 container instances for the EC2 capacity provider (Graviton, arm64)."
  type        = string
  default     = "t4g.medium"
}

variable "eta_desired" {
  description = "eta-engine tasks (always on FARGATE)."
  type        = number
  default     = 2
}

# ---- phases: set in infra/phases.auto.tfvars, one drill at a time ----

variable "routes_mode" {
  description = "routes-api capacity: fargate (all on-demand), spot (all FARGATE_SPOT: the trap), mixed (base on FARGATE + weighted FARGATE_SPOT: the fix)."
  type        = string
  default     = "fargate"

  validation {
    condition     = contains(["fargate", "spot", "mixed"], var.routes_mode)
    error_message = "routes_mode must be fargate, spot or mixed."
  }
}

variable "routes_desired" {
  description = "routes-api desired task count (Terraform sets it on every apply; with autoscaling on, the scaler moves it between applies)."
  type        = number
  default     = 2
}

variable "mixed_base" {
  description = "mixed mode: the first N routes-api tasks always go on FARGATE (on-demand)."
  type        = number
  default     = 2
}

variable "mixed_weights" {
  description = "mixed mode: weights after the base, FARGATE : FARGATE_SPOT."
  type        = object({ fargate = number, spot = number })
  default     = { fargate = 1, spot = 3 }

  validation {
    condition     = var.mixed_weights.fargate > 0 && var.mixed_weights.spot > 0
    error_message = "Both weights must be greater than 0 (a 0 weight changes the service in ways Terraform has to replace it for)."
  }
}

variable "ec2_capacity" {
  description = "Create the EC2 capacity provider: launch template, Auto Scaling group (0-4 x t4g.medium), instance role, and the eta-batch service on it."
  type        = bool
  default     = false
}

variable "ec2_tasks" {
  description = "eta-batch desired task count on the EC2 capacity provider (only with ec2_capacity = true)."
  type        = number
  default     = 0
}

variable "autoscaling" {
  description = "Service auto scaling for routes-api: target tracking on ALBRequestCountPerTarget."
  type        = bool
  default     = false
}

variable "scale_min" {
  description = "Auto scaling minimum routes-api tasks."
  type        = number
  default     = 2
}

variable "scale_max" {
  description = "Auto scaling maximum routes-api tasks."
  type        = number
  default     = 6
}

variable "scale_target" {
  description = "Target requests per routes-api task per minute (ALBRequestCountPerTarget)."
  type        = number
  default     = 300
}

variable "nlb" {
  description = "Optional: a Network Load Balancer (TCP 80) in front of the same routes-api service, as a second target group."
  type        = bool
  default     = false
}
