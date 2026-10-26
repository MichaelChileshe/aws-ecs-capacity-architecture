# Screenshots

All 28 screenshots come from the build on 26 October 2026, in the order things happened. The account ID is masked as `<account-id>`, and my public IP as `<my-ip>`.

| # | What it shows |
|---|---|
| [01](screenshots/01-preconditions.png) | State bucket; ECS service-linked role; Fargate and Fargate Spot vCPU quotas (30 each); AZs offering t4g.medium; the ECS-optimised AL2023 arm64 AMI |
| [02](screenshots/02-init-and-gates.png) | init, fmt, validate, tflint clean; checkov 101 passed / 25 failed |
| [03](screenshots/03-base-apply-outputs.png) | `Apply complete! Resources: 34 added`, outputs |
| [04](screenshots/04-alb-saved-state-36.png) | ALB name saved, app code hashes, state list 36 |
| [05](screenshots/05-ecs-status-base.png) | Both services 2/2 on FARGATE across 2 AZs, targets healthy |
| [06](screenshots/06-route-call.png) | One `/route` call: routes-api → eta-engine through Service Connect, with task, AZ and the eta-engine task role |
| [07](screenshots/07-whoami-roles.png) | Task role yes, execution role no, no keys; Service Connect 20/20; `/admin` blocked at the ALB |
| [08](screenshots/08-loadgen-baseline.png) | Baseline load 2400/2400 OK |
| [09](screenshots/09-spot-trap-plan.png) | The trap plan: FARGATE → FARGATE_SPOT, desired 2 → 4, 1 to change |
| [10](screenshots/10-spot-trap-apply.png) | Apply complete, 1 changed |
| [11](screenshots/11-spot-status-4-on-spot.png) | routes-api 4/4 on FARGATE_SPOT |
| [12](screenshots/12-spot-trap-reclaim-live.png) | The reclaim live: the 15-second samples at +210 and +225 s of load are all 503s; recovery after 95.1 s |
| [13](screenshots/13-spot-trap-summary.png) | Trap summary: 69.23% in the reclaim window, 28 s with no successful request |
| [14](screenshots/14-fix-plan.png) | The fix plan: base 2 FARGATE + weight 1:3 FARGATE_SPOT, desired 6 |
| [15](screenshots/15-fix-split-3-3.png) | Split 3 FARGATE / 3 FARGATE_SPOT, 6/6 healthy |
| [16](screenshots/16-ec2-eni-limit.png) | t4g.medium: 3 ENIs, trunking disabled → 2 tasks per instance |
| [17](screenshots/17-ec2-scaleout.png) | Scale-out to 6 tasks: 3 instances, all in us-east-1b (times on screen are from the deployment's last update; see [EC2 capacity](ec2-capacity.md)) |
| [18](screenshots/18-ec2-network-interfaces.png) | Interfaces per instance: 1 own + 1 per task |
| [19](screenshots/19-ec2-ssm-host-view.png) | Through SSM: the host sees only ens5/docker0/ecs-bridge/veth; ECS agent metadata |
| [20](screenshots/20-after-ec2-removal-check.png) | After the EC2 incident: no project instances left, no changes, state 36 |
| [21](screenshots/21-autoscaling-plan.png) | Autoscaling plan: 2 to add, 1 to change |
| [22](screenshots/22-autoscaling-watch.png) | Scale-out to 6 under 50 requests/s |
| [23](screenshots/23-autoscaling-summary.png) | Scale-out +292 s, scale-in +1015 s, 21600/21600 OK |
| [24](screenshots/24-nlb-plan.png) | NLB plan: 7 to add, 1 to change |
| [25](screenshots/25-alb-vs-nlb.png) | ALB vs NLB: headers, `/admin` 403 vs 404, both 20/20 |
| [26](screenshots/26-prices.png) | Price List API prices and cost per 1,000 task-hours |
| [27](screenshots/27-destroy-43.png) | `Destroy complete! Resources: 43 destroyed.` |
| [28](screenshots/28-clean-check.png) | Clean check: nothing left, empty state of 571 bytes |

The screenshots of the stuck EC2 removal and its recovery aren't here. That incident is documented from my terminal output and the apply logs in [the debugging journey](debugging-journey.md).
