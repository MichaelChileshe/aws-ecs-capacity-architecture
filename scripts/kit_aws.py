"""Shared helpers for the repo's scripts: call the AWS CLI the same way everywhere, fail loudly,
read Terraform outputs, talk HTTP to the load balancers, and keep results in build/results/."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# The repo folder (where env.sh lives). env.sh exports WORKDIR; fall back to the parent of scripts/.
ROOT = os.environ.get("WORKDIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "build", "results")
REGION = os.environ.get("REGION", "us-east-1")


def aws(*args):
    """Run `aws <args> --output json` and return parsed JSON (or {} for empty output)."""
    ok, data, err = aws_try(*args)
    if not ok:
        sys.exit(f"\nFAILED: aws {' '.join(args[:2])}\n{err}\n")
    return data


def aws_try(*args, timeout=120):
    """Like aws(), but returns (ok, data, stderr) instead of exiting."""
    try:
        res = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {}, f"aws {' '.join(args[:2])} did not answer within {timeout} s"
    if res.returncode != 0:
        return False, {}, res.stderr.strip()
    return True, (json.loads(res.stdout) if res.stdout.strip() else {}), ""


_outputs = {}


def tf_outputs():
    """All Terraform outputs as {name: value} (one terraform call per script run)."""
    if not _outputs:
        res = subprocess.run(["terraform", f"-chdir={os.path.join(ROOT, 'infra')}", "output", "-json"],
                             capture_output=True, text=True)
        if res.returncode != 0:
            sys.exit(f"\nFAILED: terraform output\n{res.stderr.strip()}\n")
        data = json.loads(res.stdout or "{}")
        if not data:
            sys.exit("\nterraform output is empty: has infra/ been applied?\n")
        _outputs.update({k: v["value"] for k, v in data.items()})
    return _outputs


def tf_output(name):
    return tf_outputs().get(name)


def write_result(name, obj):
    """build/results/<name>.json, pretty-printed (these files go into the repo)."""
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, f"{name}.json")
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    return path


def write_jsonl(name, records):
    """build/results/<name>.jsonl: the raw records, one JSON object per line (rebuild summaries later)."""
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, f"{name}.jsonl")
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")
    return path


def read_json(name):
    p = os.path.join(RESULTS, f"{name}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def read_jsonl(name):
    p = os.path.join(RESULTS, f"{name}.jsonl")
    return [json.loads(l) for l in open(p) if l.strip()] if os.path.exists(p) else []


def result_lines(text, prefix="RESULT "):
    """The JSON objects printed on lines that start with prefix."""
    return [json.loads(l[len(prefix):]) for l in text.splitlines() if l.startswith(prefix)]


# ---- time ----

def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ts(value):
    """AWS CLI timestamps (ISO 8601 with an offset or Z, or epoch seconds/ms) -> epoch seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def pct(values, p):
    """Nearest-rank percentile of a list (None if empty)."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = max(0, min(len(v) - 1, int(round(p / 100 * len(v) + 0.5)) - 1))
    return round(v[k], 1)


# ---- HTTP to the load balancers ----

def http_get(url, timeout=5.0):
    """GET url -> dict(status, ms, json or text, error). Never raises."""
    t0 = time.time()
    rec = {"url": url, "status": None, "ms": None, "json": None, "text": None, "error": None}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body, rec["status"] = r.read(), r.status
    except urllib.error.HTTPError as e:
        body, rec["status"] = e.read(), e.code
    except (urllib.error.URLError, OSError) as e:
        rec["error"] = type(getattr(e, "reason", e)).__name__
        rec["ms"] = round((time.time() - t0) * 1000, 1)
        return rec
    rec["ms"] = round((time.time() - t0) * 1000, 1)
    text = body.decode(errors="replace")
    try:
        rec["json"] = json.loads(text)
    except ValueError:
        rec["text"] = text[:200]
    return rec


def clock_offset(base_url, n=5):
    """Seconds to ADD to this machine's clock to get AWS-side time, from routes-api's /whoami
    now_ms (Fargate tasks keep time with the Amazon Time Sync Service; WSL clocks can drift).
    Uses the fastest of n round trips. Returns (offset_s, rtt_ms) or (0.0, None)."""
    best = None
    for _ in range(n):
        t0 = time.time()
        r = http_get(base_url + "/whoami", timeout=5)
        t1 = time.time()
        if r["status"] == 200 and r["json"] and r["json"].get("now_ms"):
            rtt = t1 - t0
            off = r["json"]["now_ms"] / 1000 - (t0 + t1) / 2
            if best is None or rtt < best[1]:
                best = (off, rtt)
    return (round(best[0], 3), round(best[1] * 1000, 1)) if best else (0.0, None)


# ---- ECS ----

def short(arn):
    return (arn or "").split("/")[-1]


def service_tasks(cluster, service, desired="RUNNING"):
    """describe-tasks for every task of a service with that desired status."""
    arns = aws("ecs", "list-tasks", "--cluster", cluster, "--service-name", service,
               "--desired-status", desired).get("taskArns", [])
    tasks = []
    for i in range(0, len(arns), 100):
        tasks += aws("ecs", "describe-tasks", "--cluster", cluster, "--tasks", *arns[i:i + 100]).get("tasks", [])
    return tasks


def task_row(t):
    return {"task": short(t["taskArn"]), "provider": t.get("capacityProviderName", "-"),
            "launch_type": t.get("launchType", "-"), "az": t.get("availabilityZone", "-"),
            "last_status": t.get("lastStatus"), "desired_status": t.get("desiredStatus"),
            "health": t.get("healthStatus", "-"), "started": t.get("startedAt"),
            "container_instance": short(t.get("containerInstanceArn")) or None}


def split_by_provider(tasks):
    out = {}
    for t in tasks:
        k = t.get("capacityProviderName", "-")
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def describe_service(cluster, service):
    s = aws("ecs", "describe-services", "--cluster", cluster, "--services", service).get("services", [])
    return s[0] if s and s[0].get("status") != "INACTIVE" else None


def target_health(tg_arn):
    """{state: count} and the raw descriptions for a target group."""
    d = aws("elbv2", "describe-target-health", "--target-group-arn", tg_arn).get("TargetHealthDescriptions", [])
    counts = {}
    for x in d:
        st = x["TargetHealth"]["State"]
        counts[st] = counts.get(st, 0) + 1
    return counts, d


# ---- CloudWatch Logs ----

def log_events(group, stream, token=None):
    """One forward page of a log stream. Returns (events, next_token); ([], token) if the stream
    doesn't exist yet."""
    args = ["logs", "get-log-events", "--log-group-name", group, "--log-stream-name", stream, "--limit", "10000"]
    args += ["--next-token", token] if token else ["--start-from-head"]
    ok, data, err = aws_try(*args)
    if not ok:
        if "ResourceNotFoundException" in err:
            return [], token
        sys.exit(f"\nFAILED: aws logs get-log-events\n{err}\n")
    return data.get("events", []), data.get("nextForwardToken", token)


def read_stream(group, stream, token=None):
    """Every event from token (or the start) to the current end. Returns (events, last_token)."""
    out = []
    for _ in range(500):                   # a page holds up to 10,000 events: far more than enough
        ev, nxt = log_events(group, stream, token)
        out += ev
        if nxt == token:                   # same token back = end of the stream (for now)
            break
        token = nxt
    return out, token
