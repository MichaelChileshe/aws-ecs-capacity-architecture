# ADR 0002: A task role per service, separate from the execution role

**Status:** accepted

## Context
ECS uses an execution role to pull images and write logs. The application's code gets the task role. Mixing them up gives the app the platform's permissions, or the platform the app's.

## Decision
- **One shared execution role** with only `AmazonECSTaskExecutionRolePolicy`.
- **Its own task role for each service.** These have no permissions, because the code calls no AWS API.
- **Trust limited** to ECS tasks in this account, with `aws:SourceArn` and `aws:SourceAccount`.

## Consequences
- **`/whoami` proved it:** each service's code ran as its own task role, never the execution role, and no credentials ever appeared in a response.
- **Adding an AWS permission later** means changing exactly one service's role.
