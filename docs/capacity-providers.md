# Capacity providers: the Fargate Spot trap and the base + weight fix

## The three strategies

`routes_mode` in `infra/phases.auto.tfvars` sets the `routes-api` service's capacity provider strategy. Each change was `Plan: 0 to add, 1 to change, 0 to destroy.`, an in-place update of `aws_ecs_service.routes` that started a new deployment.

| Mode | Strategy | Desired | Split measured |
|---|---|---|---|
| `fargate` (baseline) | FARGATE weight 1 | 2 | `{'FARGATE': 2}` |
| `spot` (the trap) | FARGATE_SPOT weight 1 | 4 | `{'FARGATE_SPOT': 4}`, 2 per AZ |
| `mixed` (the fix) | FARGATE base 2 weight 1, FARGATE_SPOT weight 3 | 6 | `{'FARGATE': 3, 'FARGATE_SPOT': 3}` |

**How the fix split works:**
- The `base` places the first 2 tasks on FARGATE.
- The remaining 4 are split by weight, 1:3, which gives 1 more on FARGATE and 3 on FARGATE_SPOT.
- Total: 3 / 3, which is what ECS placed (screenshot [`15`](screenshots/15-fix-split-3-3.png)).

`eta-engine` stayed on FARGATE throughout.

## Baseline load

The load generator runs as a Fargate task inside the VPC and calls `/route` through the ALB. It sent 20 requests a second for 120 s: **2400/2400 OK**, p50 5.6 ms, p90 7.2 ms, p99 29.1 ms (screenshot [`08`](screenshots/08-loadgen-baseline.png)).

## The trap: 100% Fargate Spot, all reclaimed at once

`spot_drill.py --label trap` started the load generator at 20 requests/s for 300 s. After 168 s of steady load (3360/3360 OK), it **stopped all 4 FARGATE_SPOT tasks at the same moment**, then measured until all 4 replacements were running and healthy.

Real Fargate Spot interruptions can't be triggered on demand. `StopTask` gives a task the same shutdown a Spot interruption ends with, minus the two-minute warning: deregistration from the target group, then SIGTERM, then up to `stopTimeout` (30 s here) to finish, then SIGKILL. Stopping all of them at once is the worst case, a capacity pool reclaimed in one go. ECS reported **0 real Spot interruptions** during the build, and every stopped task had the stop code `UserInitiated`.

Data: [`results/raw/spot-trap.json`](results/raw/spot-trap.json), [`results/raw/loadgen-spot-trap.jsonl`](results/raw/loadgen-spot-trap.jsonl). Screenshots [`11`](screenshots/11-spot-status-4-on-spot.png)–[`13`](screenshots/13-spot-trap-summary.png).

| Seconds after the reclaim (10:34:58.538 UTC) | Observed |
|---|---|
| 0 | 4 tasks stopped. ECS shows them `DEACTIVATING`, desired `STOPPED`. |
| +19 | 0/4 running, 0 pending, but all 4 targets still listed `healthy`. Every request still OK. |
| +32.0 | **First error: HTTP 503 from the ALB** |
| +62.0 | Last error |
| +64 | 4 replacement tasks pending, 1 target healthy |
| +95.1 | **Recovered:** 4/4 running, every target healthy |

| Reclaim window (97 s, reclaim → recovered) | Value |
|---|---|
| Requests | 1940 sent, 1343 OK, **597 × 503** |
| Availability | **69.23%** |
| Seconds with errors | 31 |
| Longest run with **no** successful request | **28 s** |
| Whole 300 s run | 5403/6000 OK (90.05%), p50 5.7 ms |

A dispatcher would have seen about half a minute in which nothing worked, then a recovery. The replacements land on the same FARGATE_SPOT strategy, so a second reclaim would cause the same again.

## The fix, and what I didn't measure

With `routes_mode = "mixed"`, `routes-api` ran 6 tasks: 3 on FARGATE (the base 2 plus 1 by weight) and 3 on FARGATE_SPOT. All 6 were healthy behind the ALB. A reclaim of every Spot task would leave the 3 on-demand tasks serving.

**I didn't run the reclaim drill on this configuration** (`spot_drill.py --label fix`). So this repo doesn't contain a measured availability figure for the fix, and I'm not going to estimate one. The split is verified; the behaviour under a reclaim is the next measurement to take. The script and the configuration are ready for it.

## What the capacity choice means in money

[Cost model](cost-model.md). Fargate Spot's price isn't in the Price List API; its actual cost comes from Cost Explorer's usage types for the build day. On-demand Fargate (x86) is US$24.69 per 1,000 task-hours at this task size.
