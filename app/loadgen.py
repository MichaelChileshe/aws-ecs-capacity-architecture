"""Load generator that runs INSIDE AWS as a one-off Fargate task (so my home internet isn't part of
the measurement). It sends GET requests to the ALB on a fixed schedule (open loop: a slow answer
doesn't slow the sender down, just like real dispatchers and drivers) and logs to stdout, which
the awslogs driver ships to CloudWatch Logs:

  START {...}    once, the settings
  SEC {...}      once per second of schedule: sent, ok, errors by kind, latency p50/p90/p99/max,
                 and which routes-api tasks answered
  RESULT {...}   once at the end: the totals

Settings (environment variables, overridable per run with run-task --overrides):
  TARGET_URL   http://<alb dns name>          PATH_QS   /route?from=JNB&to=DBN
  PROFILE      "20:120" = 20 requests/s for 120 s; stages chain: "5:120,50:420,5:60"
  TIMEOUT      seconds per request (default 3)    LABEL   free text, echoed in every line
Python standard library only; delivered base64-encoded in LOADGEN_B64 (no image build).
"""
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = os.environ.get("TARGET_URL", "http://127.0.0.1:8080").rstrip("/") + os.environ.get("PATH_QS", "/route?from=JNB&to=DBN")
PROFILE = os.environ.get("PROFILE", "20:60")
TIMEOUT = float(os.environ.get("TIMEOUT", "3"))
LABEL = os.environ.get("LABEL", "run")
WORKERS = int(os.environ.get("WORKERS", "200"))


def parse_profile(p):
    stages = []
    for part in p.split(","):
        rps, secs = part.strip().split(":")
        stages.append((max(0, int(rps)), max(1, int(secs))))
    return stages


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = max(0, min(len(sorted_vals) - 1, int(round(p / 100 * len(sorted_vals) + 0.5)) - 1))
    return round(sorted_vals[k], 1)


def out(kind, obj):
    print(kind + " " + json.dumps(obj, separators=(",", ":")), flush=True)


def one_request(sec, buckets, lock):
    t0 = time.time()
    kind, task = None, None
    try:
        with urllib.request.urlopen(URL, timeout=TIMEOUT) as r:
            body = r.read()
            code = r.status
        if code == 200:
            try:
                task = (json.loads(body).get("routes_api") or {}).get("task_id", "")[:8] or None
            except ValueError:
                pass
        else:
            kind = str(code)
    except urllib.error.HTTPError as e:
        kind = str(e.code)
    except urllib.error.URLError as e:
        r = e.reason
        kind = "timeout" if isinstance(r, (socket.timeout, TimeoutError)) else type(r).__name__
    except (socket.timeout, TimeoutError):
        kind = "timeout"
    except OSError as e:
        kind = type(e).__name__
    ms = (time.time() - t0) * 1000
    with lock:
        b = buckets[sec]
        b["done"] += 1
        if kind is None:
            b["ok"] += 1
            b["lat"].append(ms)
            if task:
                b["tasks"][task] = b["tasks"].get(task, 0) + 1
        else:
            b["e"][kind] = b["e"].get(kind, 0) + 1


def main():
    stages = parse_profile(PROFILE)
    total_s = sum(s for _, s in stages)
    start = float(int(time.time()) + 2)  # start on a whole second, 2 s from now
    plan = []                            # (second index, target rps)
    for rps, secs in stages:
        plan += [rps] * secs
    buckets = {i: {"sent": 0, "done": 0, "ok": 0, "e": {}, "lat": [], "tasks": {}} for i in range(total_s)}
    lock = threading.Lock()
    out("START", {"label": LABEL, "url": URL, "profile": PROFILE, "seconds": total_s, "timeout_s": TIMEOUT,
                  "start": start, "host": socket.gethostname()})

    emitted = set()
    totals = {"sent": 0, "ok": 0, "e": {}, "lat": [], "first_err_t": None, "last_err_t": None}

    def emit(i):
        with lock:
            b = buckets[i]
            lat = sorted(b["lat"])
            rec = {"label": LABEL, "s": i, "t": start + i, "rps": plan[i], "sent": b["sent"], "ok": b["ok"],
                   "err": b["sent"] - b["ok"], "e": b["e"], "p50": pct(lat, 50), "p90": pct(lat, 90),
                   "p99": pct(lat, 99), "max": round(lat[-1], 1) if lat else None, "tasks": b["tasks"]}
            totals["sent"] += b["sent"]
            totals["ok"] += b["ok"]
            totals["lat"] += b["lat"]
            for k, v in b["e"].items():
                totals["e"][k] = totals["e"].get(k, 0) + v
            if rec["err"] > 0:
                totals["first_err_t"] = totals["first_err_t"] or rec["t"]
                totals["last_err_t"] = rec["t"]
            b["lat"] = []
        emitted.add(i)
        out("SEC", rec)

    stop_reporter = threading.Event()

    def reporter():
        while not stop_reporter.is_set():
            now = time.time()
            for i in range(total_s):
                if i in emitted:
                    continue
                end = start + i + 1
                b = buckets[i]
                if now > end + TIMEOUT + 1 and (b["done"] >= b["sent"] or now > end + TIMEOUT + 15):
                    emit(i)
                elif now <= end:
                    break
            stop_reporter.wait(0.5)

    rep = threading.Thread(target=reporter, daemon=True)
    rep.start()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, rps in enumerate(plan):
            for k in range(rps):
                due = start + i + k / rps
                delay = due - time.time()
                if delay > 0:
                    time.sleep(delay)
                with lock:
                    buckets[i]["sent"] += 1
                pool.submit(one_request, i, buckets, lock)
    stop_reporter.set()
    rep.join()
    for i in range(total_s):
        if i not in emitted:
            emit(i)
    lat = sorted(totals["lat"])
    sent = totals["sent"]
    out("RESULT", {"label": LABEL, "url": URL, "profile": PROFILE, "start": start, "end": time.time(),
                   "seconds": total_s, "sent": sent, "ok": totals["ok"], "err": sent - totals["ok"],
                   "errors": totals["e"], "availability_pct": round(100 * totals["ok"] / sent, 3) if sent else None,
                   "p50": pct(lat, 50), "p90": pct(lat, 90), "p99": pct(lat, 99), "max": round(lat[-1], 1) if lat else None,
                   "first_err_t": totals["first_err_t"], "last_err_t": totals["last_err_t"]})


if __name__ == "__main__":
    main()
