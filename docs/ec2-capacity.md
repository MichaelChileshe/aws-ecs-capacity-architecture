# EC2 capacity provider: scaling from zero, and the ENI limit

## What I built

`ec2_capacity = true` adds 11 resources and updates 1 (the cluster's capacity providers):
- **Instances:** a launch template with the ECS-optimised Amazon Linux 2023 arm64 AMI (from its public SSM parameter), on t4g.medium with IMDSv2, an encrypted gp3 root volume, and `ECS_AWSVPC_BLOCK_IMDS=true` so tasks can't read the instance's credentials.
- **Permissions and network:** an instance role with `AmazonEC2ContainerServiceforEC2Role` and `AmazonSSMManagedInstanceCore`, and a security group with no inbound rules.
- **An Auto Scaling group** with min 0, max 4 and desired 0.
- **A capacity provider:**
  - managed scaling on, at target capacity 100;
  - managed draining on;
  - managed termination protection off, so the lab group can always be deleted.
- **The `eta-batch` service** on that provider, in `awsvpc` mode. It's 256 CPU units / 512 MiB on ARM64, with placement spread by AZ, then binpack on memory.

AWS doesn't let one service move between Fargate and an Auto Scaling group capacity provider. That's why the batch workload is its own service.

## How many tasks fit on a t4g.medium?

`ec2_capacity.py eni` reads the real limits from `describe-instance-types` and the account's `awsvpcTrunking` setting. Data: [`results/raw/ec2-eni.json`](results/raw/ec2-eni.json). Screenshot [`16`](screenshots/16-ec2-eni-limit.png).

| Limit | Tasks per instance |
|---|---|
| CPU: 2048 units ÷ 256 | 8 |
| Memory: 3835 MiB registered ÷ 512 | about 7 |
| **Network interfaces:** 3 maximum, minus the instance's own primary | **2** |
| **Result** | **2, limited by ENIs**: 6 tasks need 3 instances (1 without the ENI limit) |

In `awsvpc` mode every task gets its own network interface. With ENI trunking disabled in this account (`awsvpcTrunking=disabled`), the number of interfaces an instance type can attach is the real ceiling. `describe-network-interfaces` confirmed it on the running instances (screenshot [`18`](screenshots/18-ec2-network-interfaces.png)):
- the instances with 2 tasks had 3 interfaces: device 0 their own, devices 1 and 2 one per task;
- the instances with 1 task had 2.

By the time I listed the interfaces, the group had a fourth instance and the 6 tasks were spread 2/2/1/1. I didn't capture why managed scaling added it.

On the host itself, `ip -br link` showed only `ens5`, `docker0`, `ecs-bridge` and `veth` devices. In `awsvpc` mode each task's interface is moved into that task's own network namespace (screenshot [`19`](screenshots/19-ec2-ssm-host-view.png)).

## Scale-out

`ec2_capacity.py watch` followed the scale-out to 6 tasks. Data: [`results/raw/ec2-scaleout.json`](results/raw/ec2-scaleout.json), [`results/raw/ec2-timeline.jsonl`](results/raw/ec2-timeline.jsonl). Screenshot [`17`](screenshots/17-ec2-scaleout.png).

**Where the clock starts.** The apply changed `eta-batch` from 0 to 6 tasks in one step, with the Auto Scaling group at 0 instances. My script timed events from the deployment's `updatedAt`. ECS keeps updating that timestamp while the deployment progresses, so it isn't the start of the step. Instead, I report times from the **first instance launch**, the earliest event in the data.

| Seconds after the first launch | Event |
|---|---|
| 0 | First instance launched (`i-053047b7ed6926254`) |
| +11.9 to +87.0 | **5 launch attempts failed:** "We currently do not have sufficien[t capacity]" (the activity text is cut short in my log) |
| +20.2 | First instance registered in ECS |
| +46.7 | First tasks running |
| +103.4 | Successful launch of the third instance (`i-0304bbf46938177e9`) |
| +115.2 | One more failed launch for insufficient capacity |
| +127.3 | All 3 instances registered in ECS |
| **+163.8** | **All 6 tasks running** |

**Placement:**
- every instance that launched successfully was in **us-east-1b**, so all 6 tasks ran in one AZ: `{'us-east-1b': 6}`;
- each instance held 2 tasks, with 1536 CPU units and 2811 MiB left over (registered 2048 / 3835).

The spread placement strategy is best effort. It can't spread across AZs where there's no capacity.

`CapacityProviderReservation`, the metric managed scaling acts on, peaked at **300** (per-minute maximums 100, 100, 100, 200, 100, 300, 200). That means ECS was asking for three times the capacity it had while tasks waited to be placed.

**What this taught me:** a single instance type is a single point of failure for capacity. For the real batch fleet I'd use a mixed-instances policy (several Graviton sizes and families), and accept that "spread by AZ" depends on what AWS can launch at that moment.

## Removing it

Removing this capacity is where my configuration failed. The ECS agents lost their permissions and their outbound rule before their tasks could stop. See the [debugging journey](debugging-journey.md). The fix in the code: the Auto Scaling group `depends_on` the policy attachments and the egress rule, so they're removed last.
