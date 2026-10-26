#!/usr/bin/env python3
"""Who does the application code run as, and does Service Connect work?

  python3 scripts/whoami.py [--n 20]

1. GET /whoami through the ALB n times: the RoleArn routes-api reads from the container
   credentials endpoint, compared with the Terraform outputs (task role vs execution role).
2. GET /route n times: routes-api calls http://eta:8080 through Service Connect; each answer names
   the routes-api task and the eta-engine task (and eta-engine's own role).
3. GET /admin/test: the ALB answers 403 itself (listener rule), the request never reaches a task.
Saved to build/results/whoami.json.
"""
import argparse
import sys

from kit_aws import clock_offset, http_get, pct, tf_outputs, write_result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    o = tf_outputs()
    base = f'http://{o["alb_dns"]}'
    roles = o["roles"]
    out = {"alb": o["alb_dns"], "expected": roles}

    who = [http_get(base + "/whoami") for _ in range(a.n)]
    ok = [w["json"] for w in who if w["status"] == 200 and w["json"]]
    if not ok:
        sys.exit(f"no /whoami answers from {base}: first result {who[0]}")
    routes_roles = sorted({w.get("role_arn") or "none" for w in ok})
    out["routes_api"] = {
        "answers": f"{len(ok)}/{a.n}", "role_arns_seen": routes_roles,
        "tasks_seen": sorted({w["task_id"][:12] for w in ok}),
        "launch_types": sorted({w["launch_type"] for w in ok}), "azs": sorted({w["az"] for w in ok}),
        "is_task_role": routes_roles == [roles["routes_api"]],
        "is_execution_role": roles["execution"] in routes_roles,
        "keys_in_response": any(k in w for w in ok for k in ("AccessKeyId", "SecretAccessKey", "Token")),
    }

    rt = [http_get(base + "/route?from=JNB&to=DBN") for _ in range(a.n)]
    good = [r["json"] for r in rt if r["status"] == 200 and r["json"]]
    eta_roles = sorted({(g.get("eta_engine") or {}).get("role_arn") or "none" for g in good})
    out["service_connect"] = {
        "answers": f"{len(good)}/{a.n}", "errors": sorted({str(r["status"] or r["error"]) for r in rt if r["status"] != 200}),
        "eta_tasks_seen": sorted({(g.get("eta_engine") or {}).get("task_id", "?")[:12] for g in good}),
        "pairs": sorted({f'{g["routes_api"]["task_id"][:8]}->{(g.get("eta_engine") or {}).get("task_id", "?")[:8]}' for g in good}),
        "eta_call_ms_p50": pct([g.get("eta_call_ms") for g in good], 50),
        "eta_role_arns_seen": eta_roles, "eta_is_its_task_role": eta_roles == [roles["eta_engine"]],
        "sample": good[0] if good else None,
    }
    adm = http_get(base + "/admin/test")
    out["admin_path"] = {"status": adm["status"], "body": (adm["text"] or str(adm["json"]))[:60]}
    off, rtt = clock_offset(base)
    out["clock_offset_s"], out["clock_rtt_ms"] = off, rtt

    r, s = out["routes_api"], out["service_connect"]
    print(f'routes-api /whoami: {r["answers"]} answers from tasks {r["tasks_seen"]} ({", ".join(r["launch_types"])}, {", ".join(r["azs"])})')
    print(f'  code runs as: {" ".join(r["role_arns_seen"])}')
    print(f'  = routes-api TASK role: {"YES" if r["is_task_role"] else "NO"}    = execution role: '
          f'{"YES (wrong!)" if r["is_execution_role"] else "NO (correct)"}    keys in response: {r["keys_in_response"]}')
    print(f'Service Connect /route: {s["answers"]} ok, eta-engine tasks seen: {s["eta_tasks_seen"]}, '
          f'eta call p50 {s["eta_call_ms_p50"]} ms, errors: {s["errors"] or "none"}')
    print(f'  routes->eta pairs: {" ".join(s["pairs"])}')
    print(f'  eta-engine code runs as: {" ".join(s["eta_role_arns_seen"])} = its TASK role: '
          f'{"YES" if s["eta_is_its_task_role"] else "NO"}')
    print(f'ALB /admin/test: {out["admin_path"]["status"]} "{out["admin_path"]["body"]}"')
    print(f'clock: this machine is {off:+.3f} s off AWS time (round trip {rtt} ms)')
    print("saved", write_result("whoami", out))


if __name__ == "__main__":
    main()
