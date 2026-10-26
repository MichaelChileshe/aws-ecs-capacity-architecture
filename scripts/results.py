#!/usr/bin/env python3
"""Every measured number from build/results/ in one list (and build/results/summary.csv).
Anything not measured yet shows as "-".

  python3 scripts/results.py
"""
import csv
import os
import re

from kit_aws import RESULTS, read_json


def main():
    rows = []

    def add(section, item, value):
        rows.append((section, item, "-" if value in (None, "", [], {}) else value))

    files = sorted(os.listdir(RESULTS)) if os.path.isdir(RESULTS) else []

    for name in files:
        if name.startswith("ecs-status-") and name.endswith(".json"):
            s = read_json(name[:-5])
            for svc, v in s["services"].items():
                add("ECS status", f'{name[11:-5]}: {svc} desired / running / split',
                    f'{v["desired"]} / {v["running"]} / {v["split"]}')
            add("ECS status", f'{name[11:-5]}: target health', s["target_groups"])

    w = read_json("whoami")
    if w:
        r, sc = w["routes_api"], w["service_connect"]
        add("Identity", "routes-api code runs as its TASK role / as the execution role / keys returned",
            f'{r["is_task_role"]} / {r["is_execution_role"]} / {r["keys_in_response"]}')
        add("Identity", "eta-engine code runs as its TASK role", sc["eta_is_its_task_role"])
        add("Service Connect", "/route answers / eta tasks seen / eta call p50 ms",
            f'{sc["answers"]} / {len(sc["eta_tasks_seen"])} / {sc["eta_call_ms_p50"]}')
        add("ALB", "/admin/test answered by the ALB", f'{w["admin_path"]["status"]} {w["admin_path"]["body"]}')
        add("Clock", "WSL clock offset to AWS (s)", w.get("clock_offset_s"))

    for name in files:
        if name.startswith("loadgen-") and name.endswith(".json") and not name.endswith("-task.json"):
            l = read_json(name[:-5])
            add("Load generator", f'{name[8:-5]}: ok / sent (availability %) / errors',
                f'{l.get("ok")} / {l.get("sent")} ({l.get("availability_pct")}%) / {l.get("errors") or "none"}')
            add("Load generator", f'{name[8:-5]}: latency p50 / p90 / p99 ms', f'{l.get("p50")} / {l.get("p90")} / {l.get("p99")}')

    for label in ("trap", "fix"):
        s = read_json(f"spot-{label}")
        if not s:
            add("Spot reclaim", f"{label}: -", None)
            continue
        win = s["window"]
        add("Spot reclaim", f"{label}: split before / Spot tasks stopped at once / untouched",
            f'{s["split_before"]} / {s["stopped_count"]} / {s["untouched"]}')
        add("Spot reclaim", f"{label}: availability in the reclaim window (ok/sent)",
            f'{win["availability_pct"]}% ({win["ok"]}/{win["sent"]} over {win["seconds"]} s)')
        add("Spot reclaim", f"{label}: errors / seconds with errors / longest run with 0 ok (s)",
            f'{win["errors"] or "none"} / {win["seconds_with_errors"]} / {win["longest_zero_ok_run_s"]}')
        add("Spot reclaim", f"{label}: seconds to recovered (all running + healthy)", s["recovered_after_s"])
        add("Spot reclaim", f"{label}: zero healthy targets (s after reclaim, from-to)", s["zero_healthy_targets_s"] or "never")
        add("Spot reclaim", f"{label}: split after", s["split_after"])
        add("Spot reclaim", f"{label}: stopped tasks by stop code / REAL Spot interruptions",
            f'{s["interruption_scan"]["by_stop_code"]} / {len(s["interruption_scan"]["real_spot_interruptions"])}')

    e = read_json("ec2-eni")
    if e:
        add("EC2 capacity", f'{e["instance_type"]}: max ENIs / awsvpcTrunking', f'{e["max_network_interfaces"]} / {e["awsvpcTrunking_setting"]}')
        add("EC2 capacity", "eta-batch tasks per instance: by ENI / CPU / memory -> used",
            f'{e["tasks_per_instance_by_eni"]} / {e["tasks_per_instance_by_cpu"]} / {e["tasks_per_instance_by_memory_approx"]} -> '
            f'{e["tasks_per_instance"]} ({e["limited_by"]})')
        add("EC2 capacity", f'instances for {e["tasks"]} tasks: with / without the ENI limit',
            f'{e["instances_needed"]} / {e["instances_needed_without_eni_limit"]}')
    x = read_json("ec2-scaleout")
    if x:
        add("EC2 capacity", "ASG desired steps", " -> ".join(map(str, x["asg_desired_steps"])))
        add("EC2 capacity", "s after service update: first launch / first registered / all registered",
            f'{x["first_launch_s"]} / {x["first_registered_s"]} / {x["all_registered_s"]}')
        add("EC2 capacity", "s after service update: first task RUNNING / all RUNNING",
            f'{x["first_task_running_s"]} / {x["all_tasks_running_s"]}')
        add("EC2 capacity", "tasks per instance (placement)", ", ".join(f'{r["instance"][-6:]} {r["az"]} {r["tasks"]}' for r in x["instances"]))
        add("EC2 capacity", "tasks per AZ", x["tasks_per_az"])
    n = read_json("ec2-enis")
    if n:
        add("EC2 capacity", "network interfaces per instance (tasks)",
            ", ".join(f'{r["instance"][-6:]} {r["enis"]} ({r["running_tasks"]})' for r in n["instances"]))

    sc = read_json("scale")
    if sc:
        add("Auto scaling", "min / max / peak desired", f'{sc["min"]} / {sc["max"]} / {sc["peak_desired"]}')
        add("Auto scaling", "s after the load step: first scale-out / peak running",
            f'{sc["first_scale_out_after_step_s"]} / {sc["peak_running_after_step_s"]}')
        add("Auto scaling", "s after the load ended: first scale-in / back to min",
            f'{sc["first_scale_in_after_load_end_s"]} / {sc["back_to_min_after_load_end_s"]}')
        for a in sc["alarms"]:
            add("Auto scaling", f'alarm {a["comparison"]} {a["threshold"]}', f'{a["datapoints_to_alarm"]} of {a["evaluation_periods"]} x {a["period_s"]} s')
        add("Auto scaling", "load generator availability %", sc["loadgen"]["availability_pct"])

    lb = read_json("lb-compare")
    if lb:
        for r in lb["rows"]:
            add("ALB vs NLB", f'{r["lb"]}: DNS addresses / ok / p50 ms', f'{len(r["dns_answers"])} / {r["ok"]} / {r["ms_p50"]}')
            add("ALB vs NLB", f'{r["lb"]}: task saw peer / X-Forwarded-For', f'{r["task_saw_peer"]} / {r["x_forwarded_for"]}')
            add("ALB vs NLB", f'{r["lb"]}: /admin/test', f'{r["admin_status"]} {r["admin_body"]}')

    pr = read_json("prices")
    if pr:
        for k, v in pr["per_1000_task_hours_usd"].items():
            add("Cost per 1,000 task-hours (US$)", k, v)
        for k, v in pr["public_ipv4_per_1000_task_hours_usd"].items():
            add("Cost per 1,000 task-hours (US$)", "public IPv4: " + k, v)
        add("Cost per 1,000 task-hours (US$)", "Fargate Spot in the Price List API", pr["fargate_spot_in_price_list"])

    for name in files:
        if name.startswith("tf-") and name.endswith(".txt"):
            line = None
            for ln in open(os.path.join(RESULTS, name), errors="replace"):
                if re.search(r"^(Plan:|Destroy complete!|Apply complete!|No changes\.)", ln.strip()):
                    line = ln.strip()
            add("Terraform", name, line)

    if not rows:
        print("nothing measured yet")
        return
    width = max(len(r[1]) for r in rows)
    sec = None
    for s, i, v in rows:
        if s != sec:
            print(f"\n== {s}")
            sec = s
        print(f"  {i:<{width}}  {v}")
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, "summary.csv"), "w", newline="") as f:
        csv.writer(f).writerows([("section", "item", "value"), *rows])
    print("\nsaved build/results/summary.csv")


if __name__ == "__main__":
    main()
