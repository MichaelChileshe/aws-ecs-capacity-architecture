# Terraform: structure, gates, counts

## Layout

```
infra/versions.tf      backend (S3, key aws-ecs-capacity-architecture/, use_lockfile), provider, tags
infra/variables.tf     routes_mode / routes_desired, ec2_capacity / ec2_tasks, autoscaling, nlb, sizes
infra/network.tf       VPC 10.160.0.0/16, 2 public subnets, IGW, routes, security groups
infra/alb.tf           ALB, routes-api target group (ip, deregistration 15 s), listener, /admin block rule
infra/nlb.tf           optional NLB + target group + listener + rules
infra/ecs.tf           cluster, FARGATE/FARGATE_SPOT providers, Service Connect namespace, task definitions,
                       eta-engine and routes-api services (strategy chosen by routes_mode)
infra/ec2.tf           optional EC2 provider: role, profile, SG, launch template, ASG, capacity provider,
                       eta-batch task definition + service
infra/scaling.tf       optional target tracking on ALBRequestCountPerTarget
infra/iam.tf           execution role, two task roles (no permissions, confused-deputy trust)
infra/.terraform.lock.hcl   hashicorp/aws 6.70.0 (constraint ~> 6.60)
```

## Quality gates

| Gate | Result |
|---|---|
| `terraform fmt -check -recursive` | clean |
| `terraform validate` | `Success! The configuration is valid.` |
| `tflint --recursive` (recommended preset) | clean |
| `checkov` (soft fail, triaged below) | **Passed 101, Failed 25, Skipped 0** |

Screenshot [`02`](screenshots/02-init-and-gates.png). Full output: [`results/raw/checkov.txt`](results/raw/checkov.txt).

| What checkov flagged | Count | Decision |
|---|---|---|
| HTTP, not HTTPS, on the ALB: CKV_AWS_2, CKV_AWS_103, CKV2_AWS_20 (×2), CKV_AWS_378 | 5 | **Gap.** The lab has no domain or certificate. In production: an ACM certificate, HTTPS listener, redirect 80 → 443. |
| Public ingress on port 80: CKV_AWS_260 | 1 | Intended: it's the public entry point. |
| No WAF on the public ALB: CKV2_AWS_28 | 1 | **Gap.** Production: AWS WAF managed rules. |
| Public IPs on tasks and subnets: CKV_AWS_333 (×2), CKV_AWS_130 (×2) | 4 | **Accepted for the lab:** it avoids NAT gateway cost. Production: private subnets with NAT or VPC endpoints. |
| Logging: ALB access logs (CKV_AWS_91), VPC flow logs (CKV2_AWS_11), log group KMS (CKV_AWS_158), log retention ≥ 1 year (CKV_AWS_338), Container Insights (CKV_AWS_65) | 5 | **Gap, recorded.** Kept off for cost and cleanup. Container Insights would have helped with the EC2 incident. |
| ALB deletion protection: CKV_AWS_150 | 1 | Accepted: the lab must destroy cleanly. |
| Default security group not restricted: CKV2_AWS_12 | 1 | **Should fix:** a free `aws_default_security_group` with no rules. |
| Load generator security group not attached: CKV2_AWS_5 | 1 | False positive. It's attached at `run-task` time by the script, not in Terraform. |
| AZ data source not pinned: CKV_AWS_394 | 1 | Accepted. Worth pinning given the insufficient-capacity errors. |
| State bucket in `bootstrap/` (CKV_AWS_18, 144, 145, 300, CKV2_AWS_62) | 5 | Out of scope: it's the shared bucket from an earlier project. |

## Counts and timings

| Change | Plan / result |
|---|---|
| Base | `Plan: 34 to add`, `Apply complete! Resources: 34 added`. State list 36 (34 + 2 data sources). |
| `routes_mode = "spot"`, desired 4 | `0 to add, 1 to change`: the service updated in place |
| `routes_mode = "mixed"`, desired 6 | `0 to add, 1 to change` |
| `ec2_capacity = true` | `11 added, 1 changed`: the cluster's capacity providers |
| `ec2_tasks = 6` | `0 added, 1 changed` |
| EC2 removed | **First attempt:** planned `0 to add, 1 to change, 11 to destroy`, timed out after 20 min on the service ([debugging journey](debugging-journey.md)). **After the recovery:** `0 added, 1 changed, 6 destroyed`. |
| `autoscaling = true`, desired 2 | `2 added, 1 changed` |
| `nlb = true` | `7 added, 1 changed` |
| Destroy | **`Destroy complete! Resources: 43 destroyed.`** (the Cloud Map namespace took 38 s) |

Every apply was logged with `tee` to [`results/raw/`](results/raw/). ANSI colour codes are stripped, and the account ID and my public IP are masked.
