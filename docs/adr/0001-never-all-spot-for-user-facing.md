# ADR 0001: No user-facing service runs 100% on Fargate Spot

**Status:** accepted

## Context
Finance wanted Spot prices for `routes-api`. Fargate Spot can take tasks back with two minutes' warning, and a capacity pool can be reclaimed all at once.

## Measurement
`routes-api` was 100% on FARGATE_SPOT with 4 tasks, and I stopped all 4 at once under 20 requests/s:
- **69.23% availability** in the 97 s reclaim window;
- **28 s with no successful request** at all;
- 95.1 s to recover.

([capacity providers](../capacity-providers.md))

## Decision
User-facing services use a strategy with a **base on FARGATE**, then weights that put the rest mostly on FARGATE_SPOT. For `routes-api` that's base 2, then 1:3, which at 6 tasks gave 3 on-demand and 3 Spot.

## Consequences
- **Cheaper than all on-demand,** though not as cheap as all Spot.
- **The on-demand base keeps answering when every Spot task goes.** I verified the 3/3 split, but I haven't yet run the reclaim against it, so its availability is still to be measured.
- **Batch work that can retry,** like `eta-batch`, can go fully on Spot.
