#!/usr/bin/env python3
"""ALB vs NLB in front of the same routes-api tasks, from my laptop.

  python3 scripts/lb_compare.py [--n 20]

For each load balancer: its DNS answers (addresses), n x GET /client (status, latency, what the
TASK saw: peer address, X-Forwarded-For, trace header), GET /admin/test (the ALB's listener rule
answers 403 itself; the NLB can't look at paths, so the request reaches the app: 404), and the
target group health. Saved to build/results/lb-compare.json.
"""
import argparse
import socket
import sys
import time

from kit_aws import http_get, pct, target_health, tf_outputs, write_result


def resolve(name, tries=3):
    for _ in range(tries):
        try:
            return sorted({a[4][0] for a in socket.getaddrinfo(name.split(":")[0], 80, socket.AF_INET, socket.SOCK_STREAM)})
        except socket.gaierror:
            time.sleep(10)
    return []


def probe(name, base, n):
    rows = [http_get(base + "/client", timeout=5) for _ in range(n)]
    ok = [r for r in rows if r["status"] == 200 and r["json"]]
    adm = http_get(base + "/admin/test", timeout=5)
    return {
        "lb": name, "dns_answers": resolve(base[7:]), "ok": f"{len(ok)}/{n}",
        "ms_p50": pct([r["ms"] for r in ok], 50), "ms_p90": pct([r["ms"] for r in ok], 90),
        "task_saw_peer": sorted({r["json"]["peer"] for r in ok}),
        "x_forwarded_for": sorted({str(r["json"]["x_forwarded_for"]) for r in ok}),
        "x_forwarded_proto": sorted({str(r["json"]["x_forwarded_proto"]) for r in ok}),
        "trace_header": any(r["json"]["x_amzn_trace_id"] for r in ok),
        "tasks_answering": sorted({r["json"]["task_id"][:8] for r in ok}),
        "admin_status": adm["status"], "admin_body": (adm["text"] or str(adm["json"]) or adm["error"] or "")[:50],
        "errors": sorted({str(r["status"] or r["error"]) for r in rows if r["status"] != 200}),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    o = tf_outputs()
    if not o.get("nlb_dns"):
        sys.exit("no NLB: apply nlb = true first")
    t0 = time.time()
    while True:  # the NLB's targets must be healthy first (its health checks run every 10 s)
        c, _ = target_health(o["target_groups"]["nlb"])
        if c.get("healthy", 0) >= 1 and set(c) == {"healthy"}:
            break
        if time.time() - t0 > 600:
            sys.exit(f"NLB targets not healthy after 10 minutes: {c}")
        print(f"  NLB target group: {c or 'no targets yet'} ... checking again in 15 s", flush=True)
        time.sleep(15)
    rows = [probe("ALB", f'http://{o["alb_dns"]}', a.n), probe("NLB", f'http://{o["nlb_dns"]}', a.n)]
    th = {k: target_health(v)[0] for k, v in o["target_groups"].items() if v}
    for r in rows:
        print(f'== {r["lb"]}  DNS -> {len(r["dns_answers"])} address(es) {" ".join(r["dns_answers"])}')
        print(f'   /client {r["ok"]} ok, p50 {r["ms_p50"]} ms, p90 {r["ms_p90"]} ms, answered by tasks {r["tasks_answering"]}')
        print(f'   the task saw: peer {r["task_saw_peer"]}  X-Forwarded-For {r["x_forwarded_for"]}  '
              f'X-Forwarded-Proto {r["x_forwarded_proto"]}  X-Amzn-Trace-Id present: {r["trace_header"]}')
        print(f'   /admin/test -> {r["admin_status"]} "{r["admin_body"]}"   errors: {r["errors"] or "none"}')
    print(f"target health: {th}")
    print("saved", write_result("lb-compare", {"rows": rows, "target_health": th}))


if __name__ == "__main__":
    main()
