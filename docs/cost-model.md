# Cost

## Cost per 1,000 task-hours

`prices.py` fetched on-demand prices from the AWS Price List API (us-east-1) on the day of the build. These are prices for a `routes-api`-sized task: **0.5 vCPU / 1 GB**. Data: [`results/raw/prices.json`](results/raw/prices.json), [`results/cost-per-1000-task-hours.csv`](results/cost-per-1000-task-hours.csv). Screenshot [`26`](screenshots/26-prices.png).

| Price used | US$ | Usage type |
|---|---|---|
| Fargate vCPU-hour (x86) | 0.04048 | `USE1-Fargate-vCPU-Hours:perCPU` |
| Fargate GB-hour (x86) | 0.004445 | `USE1-Fargate-GB-Hours` |
| Fargate vCPU-hour / GB-hour (ARM) | 0.03238 / 0.00356 | `USE1-Fargate-ARM-*` |
| EC2 t4g.medium, Linux on-demand, per hour | 0.0336 | `BoxUsage:t4g.medium` |
| gp3 per GB-month | 0.08 | `EBS:VolumeUsage.gp3` |
| Public IPv4 per address-hour | 0.005 | `USE1-PublicIPv4:InUseAddress` |

| Option | US$ per 1,000 task-hours |
|---|---|
| **FARGATE on-demand (x86_64)**, what this build ran | **24.69** |
| FARGATE on-demand (ARM64), for reference | 19.75 |
| **EC2 t4g.medium on-demand, 2 tasks per instance** (limited by ENIs) | **18.44** |
| EC2 t4g.medium if 3 tasks per instance fitted (memory would allow 3 of this size) | 12.30 |
| FARGATE_SPOT | **not in the Price List API.** The real figure comes from Cost Explorer's Fargate Spot usage types for the build day. |
| *Plus public IPv4:* Fargate (1 address per task) / EC2 (1 per instance, so per 2 tasks) | +5.00 / +2.50 |

**How the EC2 figure is worked out:** one instance-hour at US$0.0336, plus its gp3 root volume, shared by the tasks that fit. With the ENI limit that's 2 tasks.

**What it means for Mvelaphanda:**
- **Fargate ARM is already 20% cheaper than Fargate x86** for the same task, with no servers to run. It's the first change to make if the image is multi-architecture.
- **EC2 only becomes clearly cheaper when tasks pack densely.** At 2 tasks per instance it's 25% below Fargate x86, and only 7% below Fargate ARM. Enabling ENI trunking (where the instance type supports it) or using bridge networking changes that maths. So does using larger instances.
- **None of these figures include Savings Plans,** which apply to Fargate as well as EC2.

## What the build cost

This is an estimate from list prices. The figure from my bill will replace it once Cost Explorer shows 26 October.

| Item | Roughly |
|---|---|
| ALB for about 3.6 h, NLB for about 30 min | ~US$0.10 + LCUs |
| Fargate tasks: 4–8 at a time for about 3.6 h, part of it on Spot | ~US$0.5 |
| EC2 t4g.medium: 2–6 instances for about 1.3 h, including the stuck removal | ~US$0.2 |
| Public IPv4: tasks, instances and load balancer nodes | ~US$0.2 |
| CloudWatch Logs and metrics, Service Connect | cents |
| **Total** | **about US$1–1.5** |
