# Debugging journey

## Before the build: a review against how AWS behaves

Before I applied anything, I reviewed the configuration and scripts against the documented behaviour of ECS, capacity providers and the AWS provider. The important catch was **removing the EC2 capacity provider.** AWS refuses to delete a capacity provider that's still associated with a cluster. With default ordering, Terraform would have tried to delete it first and failed half-way, leaving the instances running. I added `lifecycle { create_before_destroy = true }` to the capacity provider. The recovery apply's log shows the resulting order: cluster updated (14 s), then the provider deleted. The same review also corrected several outputs I'd expected to see, and made sure the network-interface results were saved before the optional SSM step.

## 1. A stale state lock and a setting written twice

I started the `ec2_tasks = 6` apply with an incomplete `tee` command and stopped it with Ctrl+C. Then I ran my two-line block again. That had two effects:
- **The tfvars file had `ec2_tasks = 6` twice.** Terraform refused it: `Attribute redefined ... Each argument may be set only once.`
- **The interrupted run had left its lock on the state:** `Error acquiring the state lock ... PreconditionFailed`, lock created 10:58:44 UTC.

**Fix:**
- removed every `ec2_tasks` line and added it back once (`sed -i '/^ec2_tasks/d'`, then `echo`), then checked the file with `cat`;
- confirmed no Terraform process was running (`ps`);
- `terraform force-unlock <lock ID>`, then applied again.

Nothing had been applied by the interrupted run.

**Lesson:** `>>` appends every time, so a re-run of a block that appends isn't harmless. Check the file before applying. And only force-unlock once you've proved no process owns the lock.

## 2. Removing the EC2 capacity hung, then timed out

**Symptom:** the apply that removed the EC2 capacity (`0 to add, 1 to change, 11 to destroy`) sat on `aws_ecs_service.eta_batch[0]: Still destroying...` for 20 minutes, then failed:

```
Error: waiting for ECS Service (...eta-batch) delete: timeout while waiting for state to become 'INACTIVE' (last state: 'DRAINING', timeout: 20m0s)
```

**What I checked, in order** (times SAST, UTC+2, as I saw them):

| Time | Check | Finding |
|---|---|---|
| 13:37 | Auto Scaling group | 6 instances listed with max 4: 2 were `Terminating:Wait` (the managed draining hook) and 4 `InService`. Desired had risen to **4**. |
| 13:46 | The service | `DRAINING`, desired 0, running 0. Last event 13:18: "has stopped 6 running tasks". |
| 13:46 | Tasks still meant to run | 0 |
| 13:50 | Tasks told to stop | **6 tasks with desired status `STOPPED` but last status still `RUNNING`**, network attachment still `ATTACHED` |
| 13:50 | Container instances | **`agentConnected False` on all 4** |
| 13:50 | Lifecycle hook | `ecs-managed-draining-termination-hook`, 3600 s, default CONTINUE |

**Root cause, from my own apply log** ([`results/raw/tf-apply-ec2-off.txt`](results/raw/tf-apply-ec2-off.txt)). In the first second of the apply, Terraform started destroying these in parallel:

```
aws_iam_role_policy_attachment.instance_ssm[0]: Destroying...
aws_iam_role_policy_attachment.instance_ecs[0]: Destroying...
aws_vpc_security_group_egress_rule.instances_out[0]: Destroying...
aws_ecs_service.eta_batch[0]: Destroying...
```

Nothing in my configuration depended on the two policy attachments or on the instances' only outbound rule, so Terraform treated them as free to delete straight away. Within 3 seconds:
- the instances' role had lost `AmazonEC2ContainerServiceforEC2Role`;
- the instances' only egress rule was gone.

By 13:50 every agent showed as disconnected. A disconnected agent can't stop its tasks. ECS kept the 6 tasks as `RUNNING` and the service could never leave `DRAINING`. The group was at its maximum of 4 instances throughout, and the instances that ECS showed as `ACTIVE` couldn't reconnect for the same reasons.

**What didn't work:** I re-attached the ECS policy by hand and watched for 11 minutes. The agents stayed disconnected. Looking back, that's consistent with the cause: the egress rule was gone too, so the agents still had no route to ECS. I hadn't spotted that yet.

**What did work** (between 14:04 and 14:24):
1. `aws ecs deregister-container-instance --force` for each of the 4 container instances. This tells ECS the instances and their tasks are gone, so the service could finish.
2. `aws autoscaling delete-auto-scaling-group --force-delete`. This terminates the instances immediately without waiting on the lifecycle hook, which stopped the billing.
3. Detached the policy I had re-attached, so Terraform could delete the role.
4. `terraform apply` again: `0 added, 1 changed, 6 destroyed`. The cluster was updated first, then the capacity provider, launch template, security group, instance profile and role deleted. Terraform had already seen the group and the service as gone.

Then I checked:
- a clean plan (`No changes`);
- state list 36, back to the base;
- no instances with the project tag.

**The fix in code** ([`infra/ec2.tf`](../infra/ec2.tf)):

```hcl
resource "aws_autoscaling_group" "ecs" {
  ...
  depends_on = [aws_iam_role_policy_attachment.instance_ecs, aws_iam_role_policy_attachment.instance_ssm,
                aws_vpc_security_group_egress_rule.instances_out]
}
```

Terraform destroys in reverse dependency order. So the attachments and the egress rule now outlive the Auto Scaling group, which itself goes after the capacity provider and the service. The instances keep their permissions and their route to ECS until they're gone. It also means that at creation, instances never launch before their permissions exist.

**Lessons:**
- **Anything an instance needs while it's being drained must be a dependency of the thing that runs the instances.** Otherwise teardown deletes it first.
- **`agentConnected` is the first thing to check** when ECS tasks won't stop.
- **When I re-attached the policy and nothing changed,** I should have gone back to the log to look for a second missing piece, not just waited longer.

## 3. The EC2 scale-out landed in one AZ

I expected 6 tasks split 4/2 across two AZs. Instead, 6 launch attempts failed with "We currently do not have sufficien[t capacity]", and every instance that did start was in us-east-1b, so all 6 tasks ran in one AZ ([EC2 capacity](ec2-capacity.md)). I report it as measured. The real-world fix is a mixed-instances policy, not hoping the spread works.

## 4. What I didn't measure

I applied the base + weight fix and verified its 3/3 split. I didn't run the reclaim drill against it. The trap's 69.23% therefore has no measured "after" in this repo ([capacity providers](capacity-providers.md)).
