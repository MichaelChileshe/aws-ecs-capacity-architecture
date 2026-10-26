# ALB vs NLB in front of the same service

**What `nlb = true` adds (7 resources, plus an update to the service):**
- an internet-facing NLB (TCP 80) with a second target group;
- the matching security group rules;
- a second `load_balancer` block on `routes-api`.

The same two tasks then sat behind both load balancers at once. `lb_compare.py` sent 20 requests through each from my laptop. Data: [`results/raw/lb-compare.json`](results/raw/lb-compare.json). Screenshot [`25`](screenshots/25-alb-vs-nlb.png).

| | ALB (layer 7) | NLB (layer 4) |
|---|---|---|
| Requests | 20/20 OK | 20/20 OK |
| Latency from my laptop, p50 / p90 | 773.8 / 828.5 ms | 718.2 / 912.4 ms |
| Peer address the task saw | ALB node private IPs (`10.160.0.179`, `10.160.1.117`) | NLB node private IPs (`10.160.0.132`, `10.160.1.167`) |
| `X-Forwarded-For` | **My public IP** (masked here) | None |
| `X-Forwarded-Proto` / `X-Amzn-Trace-Id` | `http` / present | None / absent |
| `/admin/test` | **403 "blocked at the load balancer"** | 404 from the app: the request reached the task |
| Target health | 2 healthy | 2 healthy |

**What it shows:**
- **The ALB understands HTTP.** It can route and block by path, add the client's address in a header, and trace requests. Rules like the admin block can only exist there.
- **The NLB passes TCP through.** The application has to do any HTTP-level checks itself. The NLB's strengths are elsewhere: static IPs per AZ, non-HTTP protocols, and very high connection rates. These 20 requests from home can't show any of those, and the latency difference is within the noise of a home connection.
- **The client IP didn't reach the task through the NLB.** With an IP target group, client IP preservation is off by default, so the task saw the NLB node's address. If the app needs the real client IP behind an NLB, turn on preservation, or use proxy protocol.

For `routes-api` (HTTP, path rules, header-based tracing), the ALB is the right front end. An NLB fits a TCP service or anything needing fixed addresses.
