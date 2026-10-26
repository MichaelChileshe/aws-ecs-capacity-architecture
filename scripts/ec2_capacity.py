#!/usr/bin/env python3
"""EC2 capacity provider drill: the awsvpc network-interface limit, and how long managed scaling
takes from "the service wants tasks" to "tasks RUNNING" when the Auto Scaling group starts at 0.

  python3 scripts/ec2_capacity.py eni [--tasks 6]     # the ENI math for the instance type (no instances needed)
  python3 scripts/ec2_capacity.py watch               # after ec2_tasks = N is applied: timeline until all RUNNING
  python3 scripts/ec2_capacity.py enis [--ssm]        # the network interfaces actually on each instance

Every awsvpc task gets its own elastic network interface (ENI). Without ENI trunking an instance
can hold (its maximum ENIs - 1) such tasks: the primary interface belongs to the instance.
Saved: ec2-eni.json, ec2-scaleout.json + ec2-timeline.jsonl, ec2-enis.json.
"""
import argparse
import math
import sys
import time

from kit_aws import REGION, aws, describe_service, service_tasks, short, tf_outputs, ts, utc, write_jsonl, write_result


def eni_facts(instance_type, task_cpu, task_mem, tasks):
    it = aws("ec2", "describe-instance-types", "--instance-types", instance_type)["InstanceTypes"][0]
    max_eni = it["NetworkInfo"]["MaximumNetworkInterfaces"]
    vcpu, mem = it["VCpuInfo"]["DefaultVCpus"], it["MemoryInfo"]["SizeInMiB"]
    setting = aws("ecs", "list-account-settings", "--effective-settings", "--name", "awsvpcTrunking").get("settings", [])
    trunking = setting[0]["value"] if setting else "unknown"
    by_eni = max_eni - 1
    by_cpu = vcpu * 1024 // task_cpu
    by_mem = int(mem * 0.9) // task_mem      # ~10% of memory goes to the OS and the ECS agent
    per_instance = min(by_eni, by_cpu, by_mem)
    return {"instance_type": instance_type, "max_network_interfaces": max_eni, "vcpus": vcpu, "memory_mib": mem,
            "awsvpcTrunking_setting": trunking, "task_cpu": task_cpu, "task_memory": task_mem,
            "tasks_per_instance_by_eni": by_eni, "tasks_per_instance_by_cpu": by_cpu,
            "tasks_per_instance_by_memory_approx": by_mem, "tasks_per_instance": per_instance,
            "limited_by": "ENI" if per_instance == by_eni else ("CPU" if per_instance == by_cpu else "memory"),
            "tasks": tasks, "instances_needed": math.ceil(tasks / per_instance) if per_instance else None,
            "instances_needed_without_eni_limit": math.ceil(tasks / min(by_cpu, by_mem)) if min(by_cpu, by_mem) else None}


def cmd_eni(a):
    o = tf_outputs()
    sz = o["task_size"]
    f = eni_facts(o["instance_type"], sz["ec2_cpu"], sz["ec2_memory"], a.tasks)
    print(f'{f["instance_type"]}: {f["vcpus"]} vCPU, {f["memory_mib"]} MiB, max network interfaces {f["max_network_interfaces"]}, '
          f'account setting awsvpcTrunking={f["awsvpcTrunking_setting"]}')
    print(f'eta-batch task {f["task_cpu"]} CPU units / {f["task_memory"]} MiB, awsvpc mode -> tasks per instance: '
          f'by ENI {f["tasks_per_instance_by_eni"]} (= {f["max_network_interfaces"]} - 1 primary), '
          f'by CPU {f["tasks_per_instance_by_cpu"]}, by memory ~{f["tasks_per_instance_by_memory_approx"]}')
    print(f'=> {f["tasks_per_instance"]} per instance, limited by {f["limited_by"]}: {a.tasks} tasks need '
          f'{f["instances_needed"]} instance(s) (without the ENI limit: {f["instances_needed_without_eni_limit"]})')
    print("saved", write_result("ec2-eni", f))


def snapshot(o, cluster, service):
    asg_name = o["ec2"]["asg"]
    g = aws("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", asg_name)["AutoScalingGroups"][0]
    ci_arns = aws("ecs", "list-container-instances", "--cluster", cluster).get("containerInstanceArns", [])
    cis = aws("ecs", "describe-container-instances", "--cluster", cluster, "--container-instances",
              *ci_arns).get("containerInstances", []) if ci_arns else []
    tasks = service_tasks(cluster, service)
    st = {}
    for t in tasks:
        st[t["lastStatus"]] = st.get(t["lastStatus"], 0) + 1
    return g, cis, tasks, {
        "asg_desired": g["DesiredCapacity"],
        "instances": {i["InstanceId"]: f'{i["LifecycleState"]} {i["AvailabilityZone"]}' for i in g["Instances"]},
        "in_service": sum(1 for i in g["Instances"] if i["LifecycleState"] == "InService"),
        "container_instances": sum(1 for c in cis if c["status"] == "ACTIVE"), "tasks": st}


def cmd_watch(a):
    o = tf_outputs()
    if not o.get("ec2"):
        sys.exit("ec2_capacity isn't on: apply ec2_capacity = true first")
    cluster, service = o["cluster"], o["services"]["eta_batch"]
    s = describe_service(cluster, service)
    if not s or s["desiredCount"] == 0:
        sys.exit("eta-batch desired count is 0: apply ec2_tasks = N first")
    prim = [d for d in s["deployments"] if d["status"] == "PRIMARY"][0]
    t0 = ts(prim["updatedAt"])
    want = s["desiredCount"]
    print(f"eta-batch: desired {want}, service updated at {utc(t0)} (AWS time; the clock starts there)")
    timeline, start = [], time.time()
    while True:
        g, cis, tasks, snap = snapshot(o, cluster, service)
        snap["t_local"] = utc(time.time())
        timeline.append(snap)
        print(f'  {snap["t_local"][11:19]}  ASG desired {snap["asg_desired"]}  InService {snap["in_service"]}  '
              f'registered in ECS {snap["container_instances"]}  tasks {snap["tasks"] or "none yet"}', flush=True)
        if snap["tasks"].get("RUNNING", 0) >= want:
            break
        if time.time() - start > a.deadline:
            print(f"deadline {a.deadline} s reached: saving what there is")
            break
        time.sleep(15)

    acts = aws("autoscaling", "describe-scaling-activities", "--auto-scaling-group-name", o["ec2"]["asg"]).get("Activities", [])
    acts = [x for x in acts if (ts(x["StartTime"]) or 0) >= t0 - 60]
    launches = sorted((x for x in acts if x["Description"].startswith("Launching")), key=lambda x: x["StartTime"])
    ci_rows = []
    task_by_ci = {}
    for t in tasks:
        task_by_ci.setdefault(t.get("containerInstanceArn"), []).append(t)
    for c in cis:
        az = next((x.get("value") for x in c.get("attributes", []) if x["name"] == "ecs.availability-zone"), "?")
        reg = {r["name"]: r.get("integerValue") for r in c.get("registeredResources", []) if r["name"] in ("CPU", "MEMORY")}
        rem = {r["name"]: r.get("integerValue") for r in c.get("remainingResources", []) if r["name"] in ("CPU", "MEMORY")}
        ci_rows.append({"instance": c["ec2InstanceId"], "az": az, "status": c["status"],
                        "registered_at": c.get("registeredAt"), "tasks": len(task_by_ci.get(c["containerInstanceArn"], [])),
                        "registered_cpu_mem": reg, "remaining_cpu_mem": rem})
    running = [t for t in tasks if t["lastStatus"] == "RUNNING" and t.get("startedAt")]
    started = sorted(ts(t["startedAt"]) for t in running)
    regs = sorted(ts(r["registered_at"]) for r in ci_rows if r["registered_at"])

    def rel(x):
        return round(x - t0, 1) if x else None

    metric = aws("cloudwatch", "get-metric-statistics", "--namespace", "AWS/ECS/ManagedScaling",
                 "--metric-name", "CapacityProviderReservation", "--dimensions",
                 f"Name=ClusterName,Value={cluster}", f'Name=CapacityProviderName,Value={o["ec2"]["capacity_provider"]}',
                 "--start-time", utc(t0 - 300), "--end-time", utc(time.time() + 60), "--period", "60",
                 "--statistics", "Maximum").get("Datapoints", [])
    desired_steps = []
    for snap in timeline:
        if not desired_steps or desired_steps[-1] != snap["asg_desired"]:
            desired_steps.append(snap["asg_desired"])
    per_az = {}
    for r in ci_rows:
        per_az[r["az"]] = per_az.get(r["az"], 0) + r["tasks"]
    out = {"service_updated_utc": utc(t0), "desired_tasks": want, "tasks_running": len(running),
           "asg_desired_steps": desired_steps,
           "launch_activities": [{"start_s": rel(ts(x["StartTime"])), "end_s": rel(ts(x.get("EndTime"))),
                                  "status": x["StatusCode"], "description": x["Description"][:80]} for x in launches],
           "first_launch_s": rel(ts(launches[0]["StartTime"])) if launches else None,
           "first_registered_s": rel(regs[0]) if regs else None, "all_registered_s": rel(regs[-1]) if regs else None,
           "first_task_running_s": rel(started[0]) if started else None,
           "all_tasks_running_s": rel(started[-1]) if len(started) >= want else None,
           "instances": ci_rows, "tasks_per_az": per_az,
           "capacity_provider_reservation": [{"t": x["Timestamp"], "max": x["Maximum"]}
                                             for x in sorted(metric, key=lambda d: d["Timestamp"])]}
    write_jsonl("ec2-timeline", timeline)
    print(f'\n== EC2 capacity provider scale-out (seconds after the service update)')
    print(f'  ASG desired went {" -> ".join(map(str, desired_steps))}; launches: '
          + ", ".join(f'+{x["start_s"]}s' for x in out["launch_activities"]))
    print(f'  first instance registered in ECS +{out["first_registered_s"]} s, all +{out["all_registered_s"]} s')
    print(f'  first task RUNNING +{out["first_task_running_s"]} s, all {want} RUNNING +{out["all_tasks_running_s"]} s')
    for r in ci_rows:
        print(f'  {r["instance"]}  {r["az"]:<11} tasks {r["tasks"]}  registered CPU/MEM {r["registered_cpu_mem"]}  '
              f'remaining {r["remaining_cpu_mem"]}')
    print(f'  tasks per AZ: {per_az}')
    print(f'  CapacityProviderReservation (max per minute): {[d["max"] for d in out["capacity_provider_reservation"]]}')
    print("saved", write_result("ec2-scaleout", out))


def cmd_enis(a):
    o = tf_outputs()
    if not o.get("ec2"):
        sys.exit("ec2_capacity isn't on")
    cluster = o["cluster"]
    ci_arns = aws("ecs", "list-container-instances", "--cluster", cluster).get("containerInstanceArns", [])
    if not ci_arns:
        sys.exit("no container instances registered")
    cis = aws("ecs", "describe-container-instances", "--cluster", cluster, "--container-instances", *ci_arns)["containerInstances"]
    rows = []
    for c in cis:
        iid = c["ec2InstanceId"]
        enis = aws("ec2", "describe-network-interfaces", "--filters", f"Name=attachment.instance-id,Values={iid}")["NetworkInterfaces"]
        rows.append({"instance": iid, "running_tasks": c["runningTasksCount"], "enis": len(enis),
                     "interfaces": sorted((e["Attachment"]["DeviceIndex"], e["InterfaceType"], e["PrivateIpAddress"],
                                           (e.get("Description") or "")[:40]) for e in enis)})
        print(f'{iid}: {c["runningTasksCount"]} task(s), {len(enis)} network interface(s)')
        for d in rows[-1]["interfaces"]:
            print(f"    device {d[0]}  {d[1]:<10} {d[2]:<15} {d[3]}")
    out = {"instances": rows}
    write_result("ec2-enis", out)  # saved now, so a stalled SSM part (Ctrl+C) can't lose it
    if a.ssm:
        import ssm_run
        iid = rows[0]["instance"]
        res = ssm_run.run([iid], "ip -br link | grep -v '^lo'; echo; curl -s --max-time 3 http://localhost:51678/v1/metadata", REGION)
        out["ssm"] = {"instance": iid, "output": res.get(iid)}
    print("saved", write_result("ec2-enis", out))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("eni")
    e.add_argument("--tasks", type=int, default=6)
    w = sub.add_parser("watch")
    w.add_argument("--deadline", type=int, default=1200)
    n = sub.add_parser("enis")
    n.add_argument("--ssm", action="store_true", help="also list the interfaces from inside the instance (SSM)")
    a = ap.parse_args()
    {"eni": cmd_eni, "watch": cmd_watch, "enis": cmd_enis}[a.cmd](a)


if __name__ == "__main__":
    main()
