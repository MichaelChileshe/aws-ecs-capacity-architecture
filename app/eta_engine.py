"""eta-engine: the internal service that turns a depot-to-depot request into a distance and an ETA.
Only routes-api calls it, through Service Connect (discovery name "eta", port 8080). It has no
load balancer and no public entry point.

GET /eta?from=JNB&to=DBN -> {"km": ..., "eta_minutes": ..., "answered_by": {task, AZ, launch type, role}}
GET /whoami              -> the IAM role this task's code runs as (RoleArn only, never keys)
GET /health              -> 200 "ok", or 503 "draining" after SIGTERM

The same file runs as the "eta-batch" service on the EC2 capacity provider (nightly route
recomputation workers in the scenario). Python standard library only; delivered base64-encoded
in APP_B64 like routes-api. SIGTERM handling is the same: drain, stop accepting, finish, exit 0.
"""
import json
import math
import os
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE = os.environ.get("SERVICE_NAME", "eta-engine")
PORT = int(os.environ.get("PORT", "8080"))
DRAIN_SECONDS = float(os.environ.get("DRAIN_SECONDS", "5"))

# Mvelaphanda depots: (latitude, longitude)
DEPOTS = {
    "JNB": (-26.2041, 28.0473),   # Johannesburg (City Deep)
    "PTA": (-25.7479, 28.2293),   # Pretoria
    "DBN": (-29.8587, 31.0218),   # Durban harbour
    "CPT": (-33.9249, 18.4241),   # Cape Town
    "PLZ": (-33.9608, 25.6022),   # Gqeberha
    "BFN": (-29.0852, 26.1596),   # Bloemfontein
    "ELS": (-33.0153, 27.9116),   # East London
    "MBO": (-25.4753, 30.9694),   # Mbombela
}

state = {"draining": False, "in_flight": 0, "served": 0, "sigterm_at": None}
lock = threading.Lock()
drained = threading.Event()
_identity = {}
_role = {"at": 0.0, "value": None}


def log(event, **kw):
    print(json.dumps({"t": round(time.time(), 3), "svc": SERVICE, "event": event, **kw}), flush=True)


def _get_json(url, timeout=1.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def identity():
    if not _identity:
        meta = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
        info = {"task_id": "local", "az": "local", "launch_type": "local"}
        if meta:
            try:
                t = _get_json(meta + "/task")
                info = {"task_id": t.get("TaskARN", "").split("/")[-1], "az": t.get("AvailabilityZone", "?"),
                        "launch_type": t.get("LaunchType", "?")}
            except (OSError, ValueError) as e:
                info["error"] = f"metadata: {type(e).__name__}"
                return info
        _identity.update(info)
    return dict(_identity)


def role_arn():
    """RoleArn from the container credentials endpoint, cached for 60 s. Keys never leave here."""
    if _role["value"] is not None and time.time() - _role["at"] < 60:
        return _role["value"]
    full = os.environ.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
    rel = os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
    if not full and not rel:
        return {"role_arn": None, "note": "no task role"}
    try:
        doc = _get_json(full or ("http://169.254.170.2" + rel), timeout=2.0)
        val = {"role_arn": doc.get("RoleArn")}
    except (OSError, ValueError) as e:
        return {"role_arn": None, "note": f"credentials endpoint: {type(e).__name__}"}
    _role.update(at=time.time(), value=val)
    return val


def estimate(src, dst):
    a, b = DEPOTS[src], DEPOTS[dst]
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    km = 2 * 6371 * math.asin(math.sqrt(h)) * 1.25          # straight line x road factor
    hour = time.gmtime().tm_hour + 2                        # SAST = UTC+2
    speed = 62 if hour % 24 in (7, 8, 16, 17) else 78       # truck km/h, peak vs off-peak
    return round(km, 1), round(km / speed * 60 + 20, 0)     # +20 min loading/handover


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "eta-engine/1"

    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if state["draining"]:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with lock:
            state["in_flight"] += 1
        try:
            self.handle_get()
        finally:
            with lock:
                state["in_flight"] -= 1
                state["served"] += 1

    def handle_get(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        me = {"service": SERVICE, **identity(), "draining": state["draining"]}
        if u.path == "/health":
            return self.send_json(503 if state["draining"] else 200, {"status": "draining" if state["draining"] else "ok"})
        if u.path == "/whoami":
            return self.send_json(200, {**me, **role_arn(), "now_ms": int(time.time() * 1000)})
        if u.path == "/eta":
            src, dst = q.get("from", "JNB").upper(), q.get("to", "DBN").upper()
            if src not in DEPOTS or dst not in DEPOTS:
                return self.send_json(400, {"error": "unknown depot", "depots": sorted(DEPOTS), "answered_by": me})
            km, minutes = estimate(src, dst)
            return self.send_json(200, {"km": km, "eta_minutes": minutes, "answered_by": {**me, **role_arn()}})
        return self.send_json(404, {"error": "not found", "path": u.path})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


def drain_and_stop(server):
    log("sigterm", in_flight=state["in_flight"], drain_seconds=DRAIN_SECONDS)
    time.sleep(DRAIN_SECONDS)
    server.shutdown()
    deadline = time.time() + 10
    while state["in_flight"] > 0 and time.time() < deadline:
        time.sleep(0.05)
    log("stopped", in_flight_left=state["in_flight"], served=state["served"],
        seconds_after_sigterm=round(time.time() - state["sigterm_at"], 2))
    drained.set()


def main():
    server = Server(("0.0.0.0", PORT), Handler)

    def on_sigterm(signum, frame):
        if state["draining"]:
            return
        state["draining"] = True
        state["sigterm_at"] = time.time()
        threading.Thread(target=drain_and_stop, args=(server,), daemon=True).start()

    signal.signal(signal.SIGTERM, on_sigterm)
    signal.signal(signal.SIGINT, on_sigterm)
    log("listening", port=PORT, depots=len(DEPOTS), python=sys.version.split()[0])
    server.serve_forever(poll_interval=0.2)
    drained.wait(15)
    server.server_close()
    sys.exit(0)


if __name__ == "__main__":
    main()
