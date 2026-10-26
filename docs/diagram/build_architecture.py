#!/usr/bin/env python3
"""Regenerates docs/architecture.svg + docs/architecture.png using the OFFICIAL AWS Architecture Icons.

The icon pack is not committed (AWS licenses it for diagrams, not redistribution). Download it from
https://aws.amazon.com/architecture/icons/, unzip, then:

    AWS_ICONS_DIR=/path/to/unzipped-icon-package python3 build_architecture.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from awsdiag import Diagram, GREY, INK

USR, LB, FG, SPOT, EC2C, SC, IAC, OPS = ("#2E73B8", "#8C4FFF", "#ED7100", "#D13212", "#3F8624", "#E7157B",
                                         "#7B42BC", "#7D8998")
d = Diagram(1900, 1180)
d.text(40, 40, "ECS capacity architecture: Fargate, Fargate Spot and an EC2 Auto Scaling group capacity provider, "
               "measured, built and destroyed with Terraform", 19, "bold")
d.text(40, 64, "Mvelaphanda Logistics route services  ·  us-east-1  ·  VPC 10.160.0.0/16, 2 public subnets (us-east-1a, "
               "us-east-1b)  ·  state in S3 with native locking", 13, "normal", GREY)


def node(key, x, y, title, lines=(), color=INK, size=44):
    d.icon(key, x, y, size)
    d.text(x + size + 10, y + 15, title, 12.5, "bold", color)
    for i, l in enumerate(lines):
        d.text(x + size + 10, y + 32 + i * 15, l, 11, "normal", GREY)


# ---------------------------------------------------------------- left: callers + my machine
d.box(40, 100, 300, 190, USR, fill="#F2F7FC", dashed=False)
d.text(56, 126, "Callers", 13.5, "bold", USR)
node("users", 56, 144, "Dispatchers + driver app", ["GET /route?from=JNB&to=DBN", "over HTTP to the ALB"], USR, 40)
node("client", 56, 214, "My laptop", ["whoami, lb_compare: 20 calls each", "drills and status via the AWS API"], USR, 40)

d.box(40, 320, 300, 330, IAC, fill="#F7F2FC", dashed=False)
d.text(56, 346, "My machine (WSL)", 13.5, "bold", IAC)
d.text(56, 370, "terraform plan / apply / destroy", 12, "bold", INK)
d.text(56, 388, "drills = variables in phases.auto.tfvars:", 11, "normal", GREY)
d.text(56, 404, "routes_mode fargate | spot | mixed", 11, "normal", GREY)
d.text(56, 420, "ec2_capacity, ec2_tasks, autoscaling, nlb", 11, "normal", GREY)
d.text(56, 448, "quality gates before any apply:", 12, "bold", INK)
d.text(56, 466, "fmt · validate · tflint · checkov", 11, "normal", GREY)
d.text(56, 494, "the drills run as commands:", 12, "bold", INK)
for i, l in enumerate(["ecs_status · whoami · loadgen", "spot_drill (stop all Spot tasks at once)",
                       "ec2_capacity (ENI maths, scale-out watch)", "scale_watch · lb_compare · prices",
                       "results (every number, one list)"]):
    d.text(56, 512 + i * 16, l, 11, "normal", GREY)
d.text(56, 610, "no SSH, no key pair: SSM only", 11, "normal", GREY)

# ---------------------------------------------------------------- AWS
d.group("cloud", 370, 90, 1100, 1070, "AWS Cloud")
node("s3_bucket", 1110, 100, "nkosi-tfstate-(account id)", ["state key aws-ecs-capacity-architecture/ (S3 lock)"], IAC, 34)
d.group("region", 390, 150, 1060, 990, "us-east-1")
d.group("vpc", 410, 195, 1020, 925, "mvelaphanda-routes VPC 10.160.0.0/16  (2 public subnets, 2 AZs)")

# load balancers
node("alb", 440, 250, "ALB (HTTP 80)", ["/admin/* -> 403 at the edge", "adds X-Forwarded-For + trace id",
                                       "ip targets, deregistration 15 s"], LB, 44)
node("nlb", 440, 350, "NLB (TCP 80), optional", ["same tasks, 2nd target group", "no HTTP: /admin reaches the app"],
     LB, 44)
d.line([(340, 190), (360, 190), (360, 272), (436, 272)], USR, width=2.4)

# ECS cluster
d.box(700, 225, 710, 520, FG, fill="#FFF8F1", dashed=False)
node("ecs", 716, 238, "ECS cluster mvelaphanda-routes", ["capacity providers: FARGATE · FARGATE_SPOT · EC2 ASG provider"],
     FG, 40)
node("ecs_service", 716, 300, "routes-api (service)", ["0.5 vCPU / 1 GB, x86_64, stopTimeout 30 s",
                                                       "graceful SIGTERM: health 503, 5 s drain"], FG, 40)
d.text(780, 372, "routes_mode strategies (one Terraform variable):", 11.5, "bold", INK)
d.text(780, 390, "fargate: FARGATE x2", 11, "normal", GREY)
d.text(780, 406, "spot (trap): FARGATE_SPOT x4", 11, "normal", SPOT)
d.text(780, 422, "mixed (fix): base 2 FARGATE, then 1:3 -> 3 + 3", 11, "normal", EC2C)
d.text(780, 438, "autoscaling: target 300 req/task/min, 2..6", 11, "normal", GREY)
node("fargate", 1080, 300, "Fargate / Fargate Spot", ["public IP per task (no NAT)", "x86_64 tasks"], FG, 40)
d.line([(690, 290), (712, 314)], LB, width=2.2)
d.line([(690, 372), (712, 330)], LB, width=1.6, dashed=True)

node("service_connect", 716, 470, "Service Connect", ["routes-api calls http://eta:8080", "proxy in every task"], SC, 40)
node("cloud_map", 1080, 470, "Cloud Map namespace", ["mvelaphanda-routes"], SC, 40)
d.line([(760, 352), (760, 466)], SC, width=1.8)
node("ecs_service", 716, 560, "eta-engine (service)", ["FARGATE x2, internal only", "answers through Service Connect"],
     FG, 40)
d.line([(740, 514), (740, 556)], SC, width=1.8)
node("iam_role", 1080, 560, "Roles", ["execution role: pull image, logs",
                                      "task role per service: no permissions",
                                      "/whoami proves which one runs"], OPS, 40)
node("ecs_task", 716, 650, "loadgen (one-off task)", ["inside the VPC, 5-50 req/s, RESULT to logs"], OPS, 36)
node("cloudwatch", 1080, 650, "CloudWatch", ["logs, ALB metrics, target tracking alarms",
                                             "CapacityProviderReservation"], OPS, 36)

# EC2 provider
d.box(430, 780, 980, 320, EC2C, fill="#F4F9EF", dashed=False)
node("asg", 446, 796, "EC2 capacity provider (ec2_capacity = true)", ["Auto Scaling group min 0, max 4, starts at 0",
                                                                     "managed scaling target 100 · managed draining",
                                                                     "depends_on the role attachments + egress rule"],
     EC2C, 44)
node("ec2_instance", 446, 900, "t4g.medium (ECS-optimised AL2023 arm64)", ["3 ENIs, trunking off -> 2 awsvpc tasks each",
                                                                         "IMDSv2, tasks blocked from IMDS, gp3 encrypted"],
     EC2C, 44)
node("ecs_service", 900, 796, "eta-batch (service)", ["256 CPU / 512 MiB, awsvpc", "spread by AZ, binpack memory"],
     EC2C, 40)
node("spot", 900, 900, "Measured", ["6 tasks -> 3 instances (1 without ENI limit)",
                                    "6 launches failed: insufficient capacity",
                                    "all instances in us-east-1b"], SPOT, 40)
d.line([(1045, 745), (1045, 776)], EC2C, width=1.8)
d.text(446, 1010, "Removal order matters: the first removal hung 20 min when Terraform deleted the instance role's",
       11, "normal", GREY)
d.text(446, 1026, "policy attachments and egress rule first (ECS agents disconnected). Now the ASG depends on them.",
       11, "normal", GREY)

# ---------------------------------------------------------------- measured panel
bx, by = 1490, 90
d.box(bx, by, 390, 1070, "#D5DBDB", fill="#fff", dashed=False)
d.text(bx + 14, by + 26, "Measured (one build, 26 October 2026)", 14, "bold")
res = [
    ("Baseline, all FARGATE", USR, ["20 req/s x 120 s: 2400/2400 ok", "p50 5.6 / p99 29.1 ms"]),
    ("Trap: 100% FARGATE_SPOT, 4 reclaimed", SPOT, ["69.23% available in the 97 s window", "597 x 503, 28 s with no success",
                                                    "recovered 95.1 s after the reclaim"]),
    ("Fix: base 2 FARGATE, then 1:3", EC2C, ["split 3 FARGATE / 3 FARGATE_SPOT", "reclaim on it not run (no figure)"]),
    ("Roles + Service Connect", SC, ["code runs as its TASK role: yes", "execution role: no · keys: none",
                                     "routes -> eta 20/20, p50 3.4 ms"]),
    ("EC2 provider", EC2C, ["2 tasks/instance (ENI), CPU allows 8", "all 6 running ~164 s after 1st launch",
                            "CapacityProviderReservation peak 300"]),
    ("Auto scaling (target 300/task/min)", FG, ["scale-out +292 s, 6 running +365 s",
                                               "scale-in +1015 s, back to 2 +1409 s",
                                               "21600/21600 ok"]),
    ("ALB vs NLB", LB, ["ALB: XFF + trace id, /admin 403", "NLB: no headers, /admin 404 at the app"]),
    ("US$ per 1,000 task-hours", OPS, ["Fargate x86 24.69 · Fargate ARM 19.75", "EC2 t4g.medium 18.44 (12.30 if 3 fit)",
                                       "Fargate Spot: not in Price List API"]),
    ("Terraform", IAC, ["base 34 · strategy changes 1 in place", "EC2 +11 · autoscaling +2 · NLB +7", "Destroy complete! 43 destroyed"]),
]
y = by + 58
for title, c, ls in res:
    d.text(bx + 14, y, title, 12, "bold", c)
    for l in ls:
        y += 16
        d.text(bx + 14, y, l, 11.2)
    y += 24

here = os.path.dirname(os.path.abspath(__file__))
d.save(os.path.join(here, "..", "architecture.svg"), os.path.join(here, "..", "architecture.png"))
print("wrote docs/architecture.svg and docs/architecture.png")
