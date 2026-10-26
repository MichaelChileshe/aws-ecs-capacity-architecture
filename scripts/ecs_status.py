#!/usr/bin/env python3
"""The ECS view of the platform: every service (desired / running / pending, deployment state,
capacity provider strategy), every task (capacity provider, AZ, status), and the load balancer
target health. Saved to build/results/ecs-status-<label>.json.

  python3 scripts/ecs_status.py --label base             # print once
  python3 scripts/ecs_status.py --wait --label base      # repeat every 15 s until steady (max 15 min)
"""
import argparse
import sys
import time

from kit_aws import (describe_service, service_tasks, split_by_provider, target_health, task_row, tf_outputs,
                     utc, write_result)


def snapshot():
    o = tf_outputs()
    cluster = o["cluster"]
    snap = {"taken_utc": utc(time.time()), "cluster": cluster, "routes_mode": o["routes_mode"], "services": {},
            "target_groups": {}}
    for key, name in o["services"].items():
        if not name:
            continue
        s = describe_service(cluster, name)
        if not s:
            continue
        tasks = service_tasks(cluster, name)
        snap["services"][name] = {
            "desired": s["desiredCount"], "running": s["runningCount"], "pending": s["pendingCount"],
            "strategy": [{"cp": c["capacityProvider"], "base": c.get("base", 0), "weight": c.get("weight", 0)}
                         for c in s.get("capacityProviderStrategy", [])],
            "deployments": [{"status": d["status"], "rollout": d.get("rolloutState"), "desired": d["desiredCount"],
                             "running": d["runningCount"], "pending": d["pendingCount"]} for d in s["deployments"]],
            "split": split_by_provider(tasks),
            "tasks": sorted((task_row(t) for t in tasks), key=lambda r: (r["provider"], r["az"], r["task"])),
            "last_event": (s.get("events") or [{}])[0].get("message", ""),
        }
    for key, arn in o["target_groups"].items():
        if arn:
            counts, _ = target_health(arn)
            snap["target_groups"][key] = counts
    return snap


def ready(s):
    for name, v in s["services"].items():
        if v["running"] != v["desired"] or v["pending"] or len(v["deployments"]) != 1:
            return False
        if v["deployments"][0]["rollout"] not in (None, "COMPLETED"):
            return False
        if any(t["last_status"] != "RUNNING" for t in v["tasks"]):
            return False
    want = s["services"].get("routes-api", {}).get("desired", 0)
    return all(c.get("healthy", 0) == want and sum(c.values()) == want for c in s["target_groups"].values())


def show(s):
    print(f'{s["taken_utc"]}  cluster {s["cluster"]}  routes_mode={s["routes_mode"]["mode"]}')
    for name, v in s["services"].items():
        strat = ", ".join(f'{c["cp"]} base {c["base"]} weight {c["weight"]}' for c in v["strategy"])
        deps = "; ".join(f'{d["status"]} {d["rollout"]} {d["running"]}/{d["desired"]}' for d in v["deployments"])
        print(f'  {name:<11} desired {v["desired"]} running {v["running"]} pending {v["pending"]}   [{strat}]')
        print(f'              deployments: {deps}   split: {v["split"]}')
        for t in v["tasks"]:
            print(f'      {t["task"][:12]}  {t["provider"]:<28} {t["az"]:<11} {t["last_status"]}')
    for k, c in s["target_groups"].items():
        print(f'  target group {k}: {c or "no targets"}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--label", default="now")
    ap.add_argument("--deadline", type=int, default=900, help="seconds to wait at most with --wait")
    a = ap.parse_args()
    t0 = time.time()
    while True:
        s = snapshot()
        show(s)
        if not a.wait or ready(s):
            break
        if time.time() - t0 > a.deadline:
            write_result(f"ecs-status-{a.label}", s)
            sys.exit(f"not steady after {a.deadline} s: read the deployments and the last events above "
                     f"(aws ecs describe-services ... --query 'services[].events[:5].message')")
        print("  ... not steady yet, checking again in 15 s\n", flush=True)
        time.sleep(15)
    if a.wait:
        print(f"\nsteady after {time.time() - t0:.0f} s of waiting")
    print("saved", write_result(f"ecs-status-{a.label}", s))


if __name__ == "__main__":
    main()
