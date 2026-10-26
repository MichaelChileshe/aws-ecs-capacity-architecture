#!/usr/bin/env python3
"""Spot reclaim drill: while the in-AWS load generator sends steady traffic to the ALB, stop EVERY
routes-api task that runs on FARGATE_SPOT at the same moment, then measure what the dispatchers
would have seen and how long until the service is whole again.

  python3 scripts/spot_drill.py --label trap        # routes_mode = spot  (every task is Spot)
  python3 scripts/spot_drill.py --label fix         # routes_mode = mixed (base on FARGATE)

Honest about what this is: a real Fargate Spot interruption can't be triggered on demand. ECS
StopTask takes a service task through the same stop path an interruption ends in (deregistered
from the target group, SIGTERM, up to stopTimeout, SIGKILL), and stopping all Spot tasks at once
is the worst case of a reclaim. The script also scans the stopped tasks and service events for a
REAL interruption ("Your Spot Task was interrupted.", stopCode SpotInterruption).

Steps: split before -> loadgen start -> wait until traffic flows + warm-up -> stop all Spot tasks
-> watch every 5 s until running = desired and every target healthy (deadline) -> collect the
load generator -> availability in the reclaim window -> split after -> real-interruption scan.
Saved: spot-<label>.json, spot-<label>-timeline.jsonl, loadgen-spot-<label>.json/.jsonl.
"""
import argparse
import math
import sys
import time

import loadgen
from kit_aws import (aws, clock_offset, describe_service, service_tasks, short, split_by_provider, target_health,
                     task_row, tf_outputs, utc, write_jsonl, write_result)


def window_stats(secs, t_from, t_to):
    w = [s for s in secs if t_from <= s["t"] < t_to]
    sent, ok = sum(s["sent"] for s in w), sum(s["ok"] for s in w)
    errs = {}
    for s in w:
        for k, v in s["e"].items():
            errs[k] = errs.get(k, 0) + v
    err_secs = [s for s in w if s["err"] > 0]
    longest, run = 0, 0
    for s in w:
        run = run + 1 if s["sent"] and s["ok"] == 0 else 0
        longest = max(longest, run)
    return {"seconds": len(w), "sent": sent, "ok": ok, "err": sent - ok, "errors": errs,
            "availability_pct": round(100 * ok / sent, 2) if sent else None,
            "seconds_with_errors": len(err_secs), "longest_zero_ok_run_s": longest,
            "first_error_s": round(err_secs[0]["t"] - t_from, 1) if err_secs else None,
            "last_error_s": round(err_secs[-1]["t"] - t_from, 1) if err_secs else None}


def real_interruptions(cluster, service):
    """Stopped tasks (ECS keeps them for about an hour) and recent service events."""
    stopped = service_tasks(cluster, service, desired="STOPPED")
    by_code = {}
    real = []
    for t in stopped:
        code = t.get("stopCode", "-")
        by_code[code] = by_code.get(code, 0) + 1
        if code == "SpotInterruption" or "Spot Task was interrupted" in (t.get("stoppedReason") or ""):
            real.append({"task": short(t["taskArn"]), "stopped_at": t.get("stoppedAt"), "reason": t.get("stoppedReason")})
    s = describe_service(cluster, service) or {}
    ev = [e["message"] for e in s.get("events", []) if "interrupt" in e["message"].lower()]
    return {"stopped_tasks_seen": len(stopped), "by_stop_code": by_code, "real_spot_interruptions": real,
            "service_events_mentioning_interruption": ev[:10]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--profile", default="20:300", help="load generator profile (default 20 req/s for 300 s)")
    ap.add_argument("--warmup", type=int, default=60, help="seconds of steady traffic before the reclaim")
    ap.add_argument("--recover-deadline", type=int, default=480)
    a = ap.parse_args()

    o = tf_outputs()
    cluster, service = o["cluster"], o["services"]["routes"]
    base = f'http://{o["alb_dns"]}'
    tg = o["target_groups"]["alb"]

    before = service_tasks(cluster, service)
    spot = [t for t in before if t.get("capacityProviderName") == "FARGATE_SPOT" and t["lastStatus"] == "RUNNING"]
    print(f"routes-api before: {split_by_provider(before)}  (routes_mode={o['routes_mode']['mode']})")
    if not spot:
        sys.exit("no RUNNING FARGATE_SPOT tasks in routes-api: apply routes_mode spot/mixed and wait for ecs_status first")
    off, rtt = clock_offset(base)
    print(f"clock offset to AWS {off:+.3f} s (round trip {rtt} ms)")

    info = loadgen.start(f"spot-{a.label}", a.profile)
    if info["seconds"] < a.warmup + 120:
        print("  warning: the profile leaves less than 2 minutes after the reclaim for the measurement")
    loadgen.wait_running(info)
    fol = loadgen.Follower(info)
    t0 = time.time()
    while not fol.secs:
        if time.time() - t0 > 180:
            sys.exit("no SEC lines from the load generator after 3 minutes: check its log stream")
        time.sleep(5)
        fol.poll()
    print(f"  traffic flowing; {a.warmup} s of steady load before the reclaim", flush=True)
    t_warm = time.time()
    while time.time() - t_warm < a.warmup:
        time.sleep(10)
        loadgen.progress(fol.poll())

    # ---- the reclaim: every Spot task at once ----
    t_stop_local = time.time()
    stopped = []
    for t in spot:
        r = aws("ecs", "stop-task", "--cluster", cluster, "--task", t["taskArn"],
                "--reason", f"spot-drill {a.label}: simulated Spot reclaim")
        stopped.append(task_row(r["task"]))
    t_stop = t_stop_local + off
    print(f"\nRECLAIM at {utc(t_stop)}: stopped {len(stopped)} FARGATE_SPOT task(s) at once "
          f"({len(before) - len(stopped)} other task(s) untouched)", flush=True)

    # ---- watch the recovery ----
    timeline, recovered, zero_healthy = [], None, []
    desired = None
    while time.time() - t_stop_local < a.recover_deadline:
        time.sleep(5)
        s = describe_service(cluster, service)
        counts, _ = target_health(tg)
        desired = s["desiredCount"]
        now = time.time() + off
        rec = {"t": round(now, 3), "since_reclaim_s": round(now - t_stop, 1), "desired": desired,
               "running": s["runningCount"], "pending": s["pendingCount"], "targets": counts}
        timeline.append(rec)
        if counts.get("healthy", 0) == 0:
            zero_healthy.append(rec["since_reclaim_s"])
        print(f'  +{rec["since_reclaim_s"]:>5.0f}s  running {rec["running"]}/{desired}  pending {rec["pending"]}  '
              f'targets {counts or "none"}', flush=True)
        loadgen.progress(fol.poll(), every=15)
        if counts.get("healthy", 0) >= desired and s["runningCount"] >= desired and \
                sum(counts.values()) == counts.get("healthy", 0) and rec["since_reclaim_s"] > 20:
            recovered = rec
            break
    if recovered:
        print(f'RECOVERED: {desired}/{desired} running and every target healthy {recovered["since_reclaim_s"]} s after the reclaim')
    else:
        print(f"NOT recovered within {a.recover_deadline} s: the numbers below use the deadline as the window end")

    res, secs = loadgen.collect(info, fol)
    t_end = recovered["t"] if recovered else t_stop + a.recover_deadline
    win = window_stats(secs, math.floor(t_stop), math.ceil(t_end) + 1)
    pre = window_stats(secs, secs[0]["t"], math.floor(t_stop))
    after = service_tasks(cluster, service)
    scan = real_interruptions(cluster, service)

    out = {"label": a.label, "routes_mode": o["routes_mode"], "clock_offset_s": off,
           "split_before": split_by_provider(before), "stopped": stopped, "stopped_count": len(stopped),
           "untouched": len(before) - len(stopped), "reclaim_utc": utc(t_stop),
           "recovered_after_s": recovered["since_reclaim_s"] if recovered else None,
           "zero_healthy_targets_s": [zero_healthy[0], zero_healthy[-1]] if zero_healthy else None,
           "window": win, "before_reclaim": pre, "whole_run": {k: res.get(k) for k in
                                                             ("sent", "ok", "err", "errors", "availability_pct", "p50", "p90", "p99")},
           "split_after": split_by_provider(after), "tasks_after": [task_row(t) for t in after],
           "interruption_scan": scan}
    write_jsonl(f"spot-{a.label}-timeline", timeline)
    print(f"\n== spot drill {a.label}")
    print(f'  before: {out["split_before"]}   stopped at once: {len(stopped)}   untouched: {out["untouched"]}')
    print(f'  steady load before the reclaim: {pre["ok"]}/{pre["sent"]} ok ({pre["availability_pct"]}%)')
    print(f'  reclaim window ({win["seconds"]} s, reclaim -> recovered): {win["ok"]}/{win["sent"]} ok = '
          f'{win["availability_pct"]}% available, errors {win["errors"] or "none"}')
    print(f'  seconds with errors {win["seconds_with_errors"]}, longest run with NO successful request '
          f'{win["longest_zero_ok_run_s"]} s, first/last error at +{win["first_error_s"]} / +{win["last_error_s"]} s')
    print(f'  zero healthy targets: {out["zero_healthy_targets_s"] or "never"} (s after reclaim)   '
          f'recovered after: {out["recovered_after_s"]} s')
    print(f'  after: {out["split_after"]}')
    print(f'  stopped tasks by stop code: {scan["by_stop_code"]}   REAL Spot interruptions: '
          f'{len(scan["real_spot_interruptions"])} {scan["real_spot_interruptions"] or ""}')
    print("saved", write_result(f"spot-{a.label}", out))


if __name__ == "__main__":
    main()
