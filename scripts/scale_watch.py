#!/usr/bin/env python3
"""Service auto scaling under load: start the in-AWS load generator with a ramp, then every 15 s
record routes-api's desired / running task count (and the capacity provider split) until it has
scaled out AND back in to the minimum after the load, or the deadline.

  python3 scripts/scale_watch.py [--profile 5:120,50:420] [--minutes 35]

Times come from AWS: the load step from the load generator's own clock, scaling decisions from
Application Auto Scaling's activity history. The target tracking alarms (created and owned by
Application Auto Scaling) are read back so their settings are measured, not assumed.
Saved: scale.json, scale-timeline.jsonl, loadgen-scale.json/.jsonl.
"""
import argparse
import sys
import time

import loadgen
from kit_aws import (aws, clock_offset, describe_service, service_tasks, split_by_provider, target_health, tf_outputs,
                     ts, utc, write_jsonl, write_result)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="5:120,50:420")
    ap.add_argument("--minutes", type=int, default=35)
    a = ap.parse_args()
    o = tf_outputs()
    cluster, service = o["cluster"], o["services"]["routes"]
    rid = f"service/{cluster}/{service}"
    tgt = aws("application-autoscaling", "describe-scalable-targets", "--service-namespace", "ecs",
              "--resource-ids", rid).get("ScalableTargets", [])
    if not tgt:
        sys.exit("routes-api has no scalable target: apply autoscaling = true first")
    lo, hi = tgt[0]["MinCapacity"], tgt[0]["MaxCapacity"]
    off, _ = clock_offset(f'http://{o["alb_dns"]}')
    stages = [tuple(int(x) for x in p.split(":")) for p in a.profile.split(",")]
    print(f"scalable target {rid}: min {lo} max {hi}; load profile {a.profile}; clock offset {off:+.3f} s")

    info = loadgen.start("scale", a.profile)
    loadgen.wait_running(info)
    fol = loadgen.Follower(info)
    timeline, peak, scaled_out, t_start = [], 0, False, time.time()
    while True:
        fol.poll()
        s = describe_service(cluster, service)
        tasks = service_tasks(cluster, service)
        counts, _ = target_health(o["target_groups"]["alb"])
        now = time.time() + off
        load_t0 = fol.secs[0]["t"] - fol.secs[0]["s"] if fol.secs else None
        last = fol.secs[-1] if fol.secs else None
        rec = {"t": round(now, 1), "utc": utc(now), "desired": s["desiredCount"], "running": s["runningCount"],
               "pending": s["pendingCount"], "healthy_targets": counts.get("healthy", 0),
               "split": split_by_provider([t for t in tasks if t["lastStatus"] == "RUNNING"]),
               "load_rps": last["rps"] if last and now - last["t"] < 30 else 0,
               "load_s": round(now - load_t0, 0) if load_t0 else None}
        timeline.append(rec)
        peak = max(peak, rec["desired"])
        scaled_out = scaled_out or rec["desired"] > lo
        print(f'  {rec["utc"][11:19]}  load {rec["load_rps"]:>3} rps  desired {rec["desired"]}  running {rec["running"]}  '
              f'pending {rec["pending"]}  healthy targets {rec["healthy_targets"]}  {rec["split"]}', flush=True)
        load_done = fol.result is not None or (time.time() - t_start > info["seconds"] + 180)
        if scaled_out and load_done and rec["desired"] == lo and rec["running"] == lo:
            print("  back at the minimum after the load: done")
            break
        if time.time() - t_start > a.minutes * 60:
            print(f"  deadline ({a.minutes} min) reached")
            break
        time.sleep(15)

    res, secs = loadgen.collect(info, fol)
    load_t0 = secs[0]["t"] - secs[0]["s"]
    step_t = load_t0 + stages[0][1] if len(stages) > 1 else load_t0
    load_end = load_t0 + sum(x[1] for x in stages)
    acts = aws("application-autoscaling", "describe-scaling-activities", "--service-namespace", "ecs",
               "--resource-id", rid).get("ScalingActivities", [])
    acts = sorted((x for x in acts if ts(x["StartTime"]) >= load_t0 - 60), key=lambda x: x["StartTime"])
    act_rows = [{"start_utc": utc(ts(x["StartTime"])), "since_step_s": round(ts(x["StartTime"]) - step_t, 0),
                 "since_load_end_s": round(ts(x["StartTime"]) - load_end, 0), "status": x["StatusCode"],
                 "description": x["Description"][:90],
                 "direction": "out" if "AlarmHigh" in x.get("Cause", "") else ("in" if "AlarmLow" in x.get("Cause", "") else "-")}
                for x in acts]
    outs = [r for r in act_rows if r["direction"] == "out" and r["since_step_s"] >= 0]
    ins = [r for r in act_rows if r["direction"] == "in" and r["since_load_end_s"] >= 0]
    peak_run_t = next((r["t"] for r in timeline if r["running"] == peak and peak > lo), None)
    back_t = next((r["t"] for r in timeline if r["t"] > load_end and r["desired"] == lo and r["running"] == lo), None)
    alarms = aws("cloudwatch", "describe-alarms", "--alarm-name-prefix", f"TargetTracking-{rid}").get("MetricAlarms", [])
    alarm_rows = [{"name": x["AlarmName"][-40:], "metric": x.get("MetricName"), "comparison": x["ComparisonOperator"],
                   "threshold": x["Threshold"], "period_s": x.get("Period"), "evaluation_periods": x["EvaluationPeriods"],
                   "datapoints_to_alarm": x.get("DatapointsToAlarm"), "state": x["StateValue"]} for x in alarms]
    out = {"profile": a.profile, "min": lo, "max": hi, "peak_desired": peak, "load_start_utc": utc(load_t0),
           "load_step_utc": utc(step_t), "load_end_utc": utc(load_end),
           "first_scale_out_after_step_s": outs[0]["since_step_s"] if outs else None,
           "peak_running_after_step_s": round(peak_run_t - step_t, 0) if peak_run_t else None,
           "first_scale_in_after_load_end_s": ins[0]["since_load_end_s"] if ins else None,
           "back_to_min_after_load_end_s": round(back_t - load_end, 0) if back_t else None,
           "activities": act_rows, "alarms": alarm_rows,
           "loadgen": {k: res.get(k) for k in ("sent", "ok", "err", "errors", "availability_pct", "p50", "p90", "p99")}}
    write_jsonl("scale-timeline", timeline)
    print(f"\n== auto scaling (min {lo}, max {hi}, peak desired {peak})")
    print(f'  load step to {stages[-1][0] if len(stages) > 1 else stages[0][0]} rps at {out["load_step_utc"][11:19]}: '
          f'first scale-out +{out["first_scale_out_after_step_s"]} s, peak running +{out["peak_running_after_step_s"]} s')
    print(f'  load ended {out["load_end_utc"][11:19]}: first scale-in +{out["first_scale_in_after_load_end_s"]} s, '
          f'back to {lo} +{out["back_to_min_after_load_end_s"]} s')
    for r in act_rows:
        print(f'  activity {r["start_utc"][11:19]} scale-{r["direction"]:<3} {r["status"]:<10} {r["description"]}')
    for x in alarm_rows:
        print(f'  alarm ...{x["name"]}: {x["metric"]} {x["comparison"]} {x["threshold"]}, '
              f'{x["datapoints_to_alarm"]} of {x["evaluation_periods"]} x {x["period_s"]} s  [{x["state"]}]')
    l = out["loadgen"]
    print(f'  load generator: {l["ok"]}/{l["sent"]} ok ({l["availability_pct"]}%), p50 {l["p50"]} / p99 {l["p99"]} ms')
    print("saved", write_result("scale", out))


if __name__ == "__main__":
    main()
