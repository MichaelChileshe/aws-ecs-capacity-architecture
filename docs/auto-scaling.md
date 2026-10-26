# Service auto scaling under a load step

**What `autoscaling = true` adds (2 resources, plus an update to the service):**
- an Application Auto Scaling target for `routes-api`, min 2, max 6;
- a target tracking policy on **`ALBRequestCountPerTarget` = 300 requests per task per minute**, with scale-out cooldown 30 s and scale-in cooldown 60 s (short for the lab).

Application Auto Scaling creates and owns the two CloudWatch alarms. `scale_watch.py` reads them back rather than assuming their settings:

| Alarm | Condition | Datapoints |
|---|---|---|
| High | `RequestCountPerTarget` > 300 | 3 of 3 × 60 s |
| Low | `RequestCountPerTarget` < 270 | 15 of 15 × 60 s |

The service was in `mixed` mode during this drill, so at the peak it ran `{'FARGATE': 3, 'FARGATE_SPOT': 3}`.

## The load

The in-VPC load generator ran 5 requests/s for 120 s, then **50 requests/s for 420 s**. At 50 requests/s, 2 tasks see 1,500 requests per task per minute: five times the target. The policy wants 10 tasks, so the maximum of 6 caps it.

Data: [`results/raw/scale.json`](results/raw/scale.json), [`results/raw/scale-timeline.jsonl`](results/raw/scale-timeline.jsonl), [`results/autoscaling-activities.csv`](results/autoscaling-activities.csv). Screenshots [`21`](screenshots/21-autoscaling-plan.png)–[`23`](screenshots/23-autoscaling-summary.png).

| UTC | Seconds | Event |
|---|---|---|
| 12:41:14 | 0 after the step | Load steps from 5 to 50 requests/s |
| 12:46:06 | **+292** after the step | Scale-out: "Setting desired count to 6." |
| | **+365** after the step | 6 tasks running and healthy |
| 12:48:14 | 0 after the load ended | Load ends |
| 13:05:09 | **+1015** after the load ended | First scale-in: desired 5 |
| 13:07:09 / 13:09:09 | | Desired 4, then 3 |
| 13:11:09 | +1375 after the load ended | Desired 2 |
| 13:11:42 | **+1409** after the load ended | 2 tasks running: back to the minimum |

**Every request succeeded: 21600/21600**, p50 6.2 ms, p90 51.2 ms, p99 93.3 ms. The p90 and p99 rose while 2 tasks carried 50 requests/s, until the new tasks arrived.

**What it shows:**
- **Scale-out took about 5 minutes,** not seconds. The alarm needs 3 consecutive minutes above target, the ALB metric arrives per minute, and new Fargate tasks need time to start and pass health checks. A load step that hurts within a minute needs headroom (a higher minimum) or scheduled scaling, not just target tracking.
- **Scale-in is deliberately cautious.** It took about 17 minutes before the first step (the low alarm needs 15 low datapoints, plus metric delay), then one task about every 2 minutes. The money spent while scaled out lasts longer than the load does.
