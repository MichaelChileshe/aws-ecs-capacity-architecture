# ADR 0003: Service Connect for service-to-service calls

**Status:** accepted

## Context
`routes-api` calls `eta-engine` on every request. Tasks are replaced constantly by Spot reclaims, scaling and deployments, so addresses can't be configured.

## Options
1. An internal ALB for `eta-engine`: another load balancer to pay for and run.
2. Cloud Map DNS service discovery: DNS caching and TTLs during task churn.
3. **ECS Service Connect:** a name (`http://eta:8080`) resolved by a proxy that ECS manages in each task, with Cloud Map behind it.

## Decision
Option 3.

## Consequences
- **Measured:** 20/20 calls OK, p50 3.4 ms, both `eta-engine` tasks used.
- **Each task now runs a proxy container,** so the task sizes (512 CPU / 1 GB) leave room for it.
- **The Cloud Map namespace was the last resource to go** in the destroy, taking 38 s.
