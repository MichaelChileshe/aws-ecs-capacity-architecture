# ADR 0004: What EC2 container instances need must outlive them

**Status:** accepted, after an incident

## Context
Removing the EC2 capacity provider timed out after 20 minutes. Terraform deleted the instance role's policy attachments and the instances' egress rule in the first seconds, because nothing depended on them. The ECS agents disconnected, so the service's tasks could never stop ([debugging journey](../debugging-journey.md)).

## Decision
- **The Auto Scaling group `depends_on`** both instance-role policy attachments and the instance egress rule.
- **The capacity provider has `create_before_destroy`,** so the cluster is updated to drop it before it's deleted.

## Consequences
- **Teardown is expected to run in a safe order:** service, then cluster update, then capacity provider, then Auto Scaling group (instances drained by a connected agent), then attachments and egress rule. No removal has exercised this yet: the final destroy ran with the EC2 capacity already off.
- **At creation,** instances never start without their permissions.
- **The general rule:** anything a workload needs while it's being drained must be a dependency of whatever runs it.
