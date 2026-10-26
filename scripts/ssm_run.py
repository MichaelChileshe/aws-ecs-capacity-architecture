#!/usr/bin/env python3
"""Run a shell command on one or more instances in one Region through SSM Run Command, wait, print the output.
No SSH, no key pair, no inbound port: the instance's SSM agent pulls the command.

  python3 scripts/ssm_run.py <region> "<shell command>" <instance-id> [<instance-id> ...]

Other scripts import run() to get the output back per instance.
"""
import json
import subprocess
import sys
import time

from kit_aws import aws


def wait_online(iids, region, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        info = aws("ssm", "describe-instance-information", "--region", region,
                   "--filters", f"Key=InstanceIds,Values={','.join(iids)}")["InstanceInformationList"]
        online = {i["InstanceId"] for i in info if i["PingStatus"] == "Online"}
        if online >= set(iids):
            return
        print(f"  waiting for {len(set(iids) - online)} instance(s) in {region} to register with Systems Manager ...",
              flush=True)
        time.sleep(15)
    sys.exit(f"instances in {region} did not come Online in SSM within {timeout // 60} minutes")


def _quiet(*args):
    r = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def run(iids, command, region, echo=True, timeout=180):
    """Run one shell command on every instance at once; return {instance_id: stdout}. Exits if any fails."""
    iids = list(iids)
    wait_online(iids, region)
    cid = aws("ssm", "send-command", "--region", region, "--instance-ids", *iids,
              "--document-name", "AWS-RunShellScript", "--comment", command[:100], "--timeout-seconds", "300",
              "--parameters", json.dumps({"commands": [command], "executionTimeout": ["300"]}))["Command"]["CommandId"]
    t0, out, failed = time.time(), {}, []
    pending, last, last_print = set(iids), {}, time.time()
    while pending:
        time.sleep(2)
        if time.time() - t0 > timeout:
            for iid in sorted(pending):
                print(f"[{region} {iid}] no result after {timeout} s, last status: {last.get(iid, 'no invocation yet')}")
            sys.exit(f"command {cid} did not finish on {', '.join(sorted(pending))}")
        if time.time() - last_print > 15:
            print("  still waiting for: " + ", ".join(f"{i} ({last.get(i, 'no invocation yet')})" for i in sorted(pending)),
                  flush=True)
            last_print = time.time()
        for iid in sorted(pending):
            res = _quiet("ssm", "get-command-invocation", "--region", region, "--command-id", cid, "--instance-id", iid)
            if res:
                last[iid] = res["Status"] + (f" / {res.get('StatusDetails')}" if res.get("StatusDetails") else "")
            if not res or res["Status"] in ("Pending", "InProgress", "Delayed"):
                continue
            pending.discard(iid)
            out[iid] = res.get("StandardOutputContent", "").rstrip()
            err = res.get("StandardErrorContent", "").rstrip()
            if echo:
                print(f"[{region} {iid}] {res['Status']} in {time.time() - t0:.0f} s")
                if out[iid]:
                    print(out[iid])
                if err:
                    print("----- stderr -----\n" + err)
            if res["Status"] != "Success":
                failed.append(iid)
    if failed:
        sys.exit(f"command failed on {', '.join(failed)}")
    return out


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    run(sys.argv[3:], sys.argv[2], sys.argv[1])


if __name__ == "__main__":
    main()
