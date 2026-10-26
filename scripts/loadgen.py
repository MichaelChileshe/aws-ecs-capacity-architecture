#!/usr/bin/env python3
"""Start the in-AWS load generator (a one-off Fargate task) and collect what it measured.

  python3 scripts/loadgen.py run --label baseline --profile 20:120     # start, follow, collect
  python3 scripts/loadgen.py start --label x --profile 20:300          # start only (prints the task id)
  python3 scripts/loadgen.py collect --label x                         # wait for it to stop, collect

PROFILE "20:120" = 20 requests/s for 120 s; stages chain with commas: "5:120,50:420".
The task logs one SEC line per second and a RESULT line at the end to CloudWatch Logs; this script
reads them back into build/results/loadgen-<label>.jsonl (every second) and loadgen-<label>.json.
"""
import argparse
import json
import sys
import time

from kit_aws import aws, read_json, read_stream, short, tf_outputs, utc, write_jsonl, write_result


def profile_seconds(p):
    return sum(int(x.split(":")[1]) for x in p.split(","))


def start(label, profile, path_qs=None):
    o = tf_outputs()
    lg = o["loadgen"]
    env = [{"name": "PROFILE", "value": profile}, {"name": "LABEL", "value": label}]
    if path_qs:
        env.append({"name": "PATH_QS", "value": path_qs})
    net = {"awsvpcConfiguration": {"subnets": lg["subnets"], "securityGroups": [lg["security_group"]],
                                   "assignPublicIp": "ENABLED"}}
    res = aws("ecs", "run-task", "--cluster", o["cluster"], "--task-definition", lg["task_definition"],
              "--capacity-provider-strategy", "capacityProvider=FARGATE,weight=1,base=0",
              "--network-configuration", json.dumps(net), "--started-by", f"loadgen-{label}"[:36],
              "--overrides", json.dumps({"containerOverrides": [{"name": "loadgen", "environment": env}]}))
    if res.get("failures") or not res.get("tasks"):
        sys.exit(f"run-task failed: {res.get('failures')}")
    arn = res["tasks"][0]["taskArn"]
    info = {"label": label, "profile": profile, "task_arn": arn, "task_id": short(arn),
            "started_local_utc": utc(time.time()), "seconds": profile_seconds(profile)}
    write_result(f"loadgen-{label}-task", info)
    print(f"loadgen {label}: task {short(arn)} started, profile {profile} ({info['seconds']} s of load)", flush=True)
    return info


def task_state(cluster, arn):
    t = aws("ecs", "describe-tasks", "--cluster", cluster, "--tasks", arn).get("tasks", [])
    return t[0] if t else {}


class Follower:
    """Reads the loadgen task's log stream incrementally."""

    def __init__(self, info):
        o = tf_outputs()
        self.group = o["loadgen"]["log_group"]
        self.stream = f'loadgen/loadgen/{info["task_id"]}'
        self.token = None
        self.secs, self.result, self.start_line = [], None, None

    def poll(self):
        events, self.token = read_stream(self.group, self.stream, self.token)
        new = []
        for e in events:
            m = e["message"]
            if m.startswith("SEC "):
                rec = json.loads(m[4:])
                self.secs.append(rec)
                new.append(rec)
            elif m.startswith("RESULT "):
                self.result = json.loads(m[7:])
            elif m.startswith("START "):
                self.start_line = json.loads(m[6:])
        return new


def progress(new, every=10):
    for r in new:
        if r["s"] % every == 0:
            print(f'  loadgen t+{r["s"]:>4}s  {r["rps"]:>3} rps  sent {r["sent"]:>3} ok {r["ok"]:>3} err {r["err"]:>3} '
                  f'p50 {r["p50"]} ms  routes-api tasks answering: {len(r["tasks"])}  {r["e"] or ""}', flush=True)


def wait_running(info, deadline=300):
    o = tf_outputs()
    t0 = time.time()
    while time.time() - t0 < deadline:
        t = task_state(o["cluster"], info["task_arn"])
        st = t.get("lastStatus")
        if st == "RUNNING":
            print(f"  loadgen task RUNNING after {time.time() - t0:.0f} s", flush=True)
            return t
        if st == "STOPPED":
            sys.exit(f'loadgen task stopped before running: {t.get("stoppedReason")}')
        print(f"  loadgen task {st} ...", flush=True)
        time.sleep(10)
    sys.exit(f"loadgen task not RUNNING after {deadline} s")


def collect(info, follower=None, extra_deadline=600):
    """Wait for the task to stop and for its RESULT line; save everything."""
    o = tf_outputs()
    f = follower or Follower(info)
    deadline = time.time() + info["seconds"] + extra_deadline
    stopped, stopped_seen = None, None
    while True:
        progress(f.poll())
        if not stopped:
            t = task_state(o["cluster"], info["task_arn"])
            if t.get("lastStatus") == "STOPPED":
                stopped, stopped_seen = t, time.time()
                print(f'  loadgen task STOPPED (exit code {(t.get("containers") or [{}])[0].get("exitCode")})', flush=True)
        if stopped and f.result:
            break
        if stopped and time.time() - stopped_seen > 120:
            print("  task stopped 2 minutes ago and still no RESULT line: collecting what is there", flush=True)
            break
        if time.time() > deadline:
            print("  deadline reached: collecting what is there", flush=True)
            break
        time.sleep(10)
    progress(f.poll())
    if not f.secs:
        sys.exit(f"no SEC lines in {f.group} / {f.stream}: is the stream there? (aws logs describe-log-streams ...)")
    write_jsonl(f'loadgen-{info["label"]}', f.secs)
    res = dict(f.result or {})
    res.update(label=info["label"], task_id=info["task_id"], seconds_logged=len(f.secs),
               exit_code=(stopped or {}).get("containers", [{}])[0].get("exitCode") if stopped else None,
               complete=bool(f.result))
    write_result(f'loadgen-{info["label"]}', res)
    if f.result:
        print(f'loadgen {info["label"]}: {res["ok"]}/{res["sent"]} ok ({res["availability_pct"]}%), errors {res["errors"] or "none"}, '
              f'p50 {res["p50"]} / p90 {res["p90"]} / p99 {res["p99"]} ms')
    else:
        print(f'loadgen {info["label"]}: no RESULT line (task cut short?); {len(f.secs)} seconds saved')
    return res, f.secs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["run", "start", "collect"])
    ap.add_argument("--label", required=True)
    ap.add_argument("--profile", default="20:120")
    ap.add_argument("--path", default=None, help="path and query, default /route?from=JNB&to=DBN")
    a = ap.parse_args()
    if a.action in ("run", "start"):
        info = start(a.label, a.profile, a.path)
        if a.action == "start":
            return
        wait_running(info)
    else:
        info = read_json(f"loadgen-{a.label}-task")
        if not info:
            sys.exit(f"no build/results/loadgen-{a.label}-task.json: start it first")
    collect(info)
    print(f"saved build/results/loadgen-{a.label}.json and .jsonl")


if __name__ == "__main__":
    main()
