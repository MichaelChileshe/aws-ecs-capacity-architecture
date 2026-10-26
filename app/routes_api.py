"""routes-api: the front API that the Mvelaphanda dispatchers and the driver app call.

GET /route?from=JNB&to=DBN  -> asks eta-engine (through Service Connect: http://eta:8080) for the
                               ETA and returns it, plus which task / AZ / launch type answered on
                               both sides.
GET /whoami                 -> the IAM role this task's code runs as (RoleArn only, never keys)
GET /client                 -> what this task saw of the caller (peer address, X-Forwarded-For)
GET /health                 -> 200 "ok", or 503 "draining" after SIGTERM

Python standard library only. The container image is a stock python image; this file arrives
base64-encoded in the APP_B64 environment variable and is run with exec (no image build).

Graceful stop: on SIGTERM the task (1) fails its health check and keeps serving for DRAIN_SECONDS
so a load balancer that hasn't finished deregistering it still gets answers, (2) stops accepting
new connections, (3) waits for requests already in flight, (4) exits 0 before stopTimeout.
"""
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE = os.environ.get("SERVICE_NAME", "routes-api")
PORT = int(os.environ.get("PORT", "8080"))
ETA_URL = os.environ.get("ETA_URL", "http://eta:8080")
DRAIN_SECONDS = float(os.environ.get("DRAIN_SECONDS", "5"))
STARTED = time.time()

state = {"draining": False, "in_flight": 0, "served": 0, "sigterm_at": None}
lock = threading.Lock()
drained = threading.Event()


def log(event, **kw):
    print(json.dumps({"t": round(time.time(), 3), "svc": SERVICE, "event": event, **kw}), flush=True)


def _get_json(url, timeout=1.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


_identity = {}


def identity():
    """Task ARN / AZ / launch type from the ECS task metadata endpoint v4 (cached: they never change)."""
    if not _identity:
        meta = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
        info = {"task_id": "local", "az": "local", "launch_type": "local", "cluster": "local"}
        if meta:
            try:
                t = _get_json(meta + "/task")
                info = {"task_id": t.get("TaskARN", "").split("/")[-1], "az": t.get("AvailabilityZone", "?"),
                        "launch_type": t.get("LaunchType", "?"), "cluster": t.get("Cluster", "").split("/")[-1]}
            except (OSError, ValueError) as e:
                info["error"] = f"metadata: {type(e).__name__}"
                return info  # don't cache a failure
        _identity.update(info)
    return dict(_identity)


def role_arn():
    """The RoleArn field of the ECS container credentials endpoint: the role the AWS SDK in this
    container would use. Only that one field leaves this function; the keys are never returned."""
    full = os.environ.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
    rel = os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
    if not full and not rel:
        return {"role_arn": None, "note": "no task role: AWS_CONTAINER_CREDENTIALS_RELATIVE_URI not set"}
    url = full or ("http://169.254.170.2" + rel)
    try:
        doc = _get_json(url, timeout=2.0)
    except (OSError, ValueError) as e:
        return {"role_arn": None, "note": f"credentials endpoint: {type(e).__name__}"}
    return {"role_arn": doc.get("RoleArn"), "expires": doc.get("Expiration"),
            "source": "container credentials endpoint (169.254.170.2)" if not full else "full URI"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "routes-api/1"

    def log_message(self, fmt, *args):  # quiet: one access line per request is too much at 50 rps
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
            self.route_request()
        finally:
            with lock:
                state["in_flight"] -= 1
                state["served"] += 1

    def route_request(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        me = {"service": SERVICE, **identity(), "draining": state["draining"]}
        if u.path == "/health":
            if state["draining"]:
                return self.send_json(503, {"status": "draining", **me})
            return self.send_json(200, {"status": "ok"})
        if u.path == "/whoami":
            return self.send_json(200, {**me, **role_arn(), "now_ms": int(time.time() * 1000),
                                        "uptime_s": round(time.time() - STARTED, 1)})
        if u.path == "/client":
            return self.send_json(200, {**me, "peer": self.client_address[0],
                                        "x_forwarded_for": self.headers.get("X-Forwarded-For"),
                                        "x_forwarded_proto": self.headers.get("X-Forwarded-Proto"),
                                        "x_amzn_trace_id": self.headers.get("X-Amzn-Trace-Id"),
                                        "host": self.headers.get("Host"), "now_ms": int(time.time() * 1000)})
        if u.path == "/route":
            src, dst = q.get("from", "JNB"), q.get("to", "DBN")
            t0 = time.time()
            try:
                eta = _get_json(f"{ETA_URL}/eta?" + urllib.parse.urlencode({"from": src, "to": dst}), timeout=2.0)
            except (OSError, ValueError) as e:
                return self.send_json(502, {"error": f"eta-engine: {type(e).__name__}", "routes_api": me})
            return self.send_json(200, {"from": src, "to": dst, "eta_minutes": eta.get("eta_minutes"),
                                        "km": eta.get("km"), "eta_call_ms": round((time.time() - t0) * 1000, 1),
                                        "routes_api": me, "eta_engine": eta.get("answered_by")})
        return self.send_json(404, {"error": "not found", "path": u.path, **me})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


def drain_and_stop(server):
    """Runs on its own thread after SIGTERM."""
    log("sigterm", in_flight=state["in_flight"], drain_seconds=DRAIN_SECONDS)
    time.sleep(DRAIN_SECONDS)          # still answering: the load balancer may not have caught up yet
    server.shutdown()                  # stop accepting new connections
    deadline = time.time() + 10
    while state["in_flight"] > 0 and time.time() < deadline:
        time.sleep(0.05)               # let requests already in flight finish
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
    log("listening", port=PORT, eta_url=ETA_URL, host=socket.gethostname(), python=sys.version.split()[0])
    server.serve_forever(poll_interval=0.2)
    drained.wait(15)                   # serve_forever returned: wait for the in-flight requests
    server.server_close()
    sys.exit(0)


if __name__ == "__main__":
    main()
