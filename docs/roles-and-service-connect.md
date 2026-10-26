# Task role vs execution role, and Service Connect

## Two roles, two jobs

| Role | Used by | Can do |
|---|---|---|
| `mvelaphanda-routes-execution` | **The ECS agent**, before and around the container | Pull the image, write logs (`AmazonECSTaskExecutionRolePolicy`) |
| `mvelaphanda-routes-routes-api-task` | **The `routes-api` code** | Nothing: the code calls no AWS API, so the role has no permissions. It exists so the service has its own identity. |
| `mvelaphanda-routes-eta-engine-task` | **The `eta-engine` and `eta-batch` code** | Nothing, for the same reason |

The task roles' trust policy only lets ECS tasks in this account assume them, using `aws:SourceArn` and `aws:SourceAccount` as confused-deputy protection.

The proof doesn't come from reading the Terraform. Each service's `/whoami` reads the ECS container credentials endpoint (`AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` at `169.254.170.2`) and returns **only the `RoleArn` field**, never the keys. `whoami.py` called it 20 times through the ALB and compared the answers with the role ARNs Terraform created. Data: [`results/raw/whoami.json`](results/raw/whoami.json). Screenshots [`06`](screenshots/06-route-call.png) and [`07`](screenshots/07-whoami-roles.png).

| Check | Result |
|---|---|
| `routes-api` answers | 20/20, from both tasks (us-east-1a and us-east-1b, FARGATE) |
| Its code runs as its **task** role | **Yes** |
| …as the execution role | **No** (correct) |
| Credentials in any response | **No** |
| `eta-engine` code runs as its own task role | **Yes** |

Mixing up these two roles is a common mistake. Give the execution role your app's permissions and every task using it gets them. Give the task role image-pull permissions and the code can read your registry. Keeping them separate means a compromised app gets only its own task role.

## Service Connect

`routes-api` calls `http://eta:8080`. That's a name, not an address. It goes through the **Service Connect** proxy that ECS adds to each task, with the Cloud Map namespace `mvelaphanda-routes` behind it.

| Check | Result |
|---|---|
| `/route` calls through the ALB | **20/20 OK** |
| `eta-engine` tasks seen | Both (`c4e1381ef1e0`, `c83859c53eca`) |
| Pairs | Each `routes-api` task reached each `eta-engine` task |
| `routes-api` → `eta-engine` call time, p50 | **3.4 ms** |
| Errors | None |

The services never learn each other's IP addresses. Tasks come and go through Spot reclaims, scaling and deployments, and the name keeps working.

## The ALB rule

`/admin/*` is blocked by a listener rule on the ALB. `whoami.py` got `403 "blocked at the load balancer"`, so the request never reached a task. The [NLB comparison](alb-vs-nlb.md) shows why that rule can only live on the ALB.
