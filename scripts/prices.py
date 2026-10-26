#!/usr/bin/env python3
"""Cost per 1,000 task-hours: FARGATE on-demand vs FARGATE_SPOT vs EC2 (t4g.medium), with on-demand
prices fetched NOW from the AWS Price List API (us-east-1 endpoint), never hard-coded.

  python3 scripts/prices.py

"A task" = the routes-api size: 0.5 vCPU / 1 GB (app + Service Connect proxy).
EC2: tasks per instance = min(by ENI, by CPU, by memory) from build/results/ec2-eni.json (or the
same calculation live), cost = instance-hours + its 30 GB gp3 root volume.
Fargate Spot: Spot prices move; if the Price List API has no Fargate Spot product for the Region,
the script says so and leaves it to Cost Explorer (usage types ...SpotUsage-Fargate-...) the day
after the build, instead of guessing a discount.
Public IPv4 (each Fargate task here has one, each EC2 instance has one) is shown separately.
Saved to build/results/prices.json.
"""
import json

from kit_aws import REGION, aws_try, read_json, tf_outputs, write_result

PREFIX = {"us-east-1": "USE1", "us-east-2": "USE2", "us-west-2": "USW2", "eu-west-1": "EU", "af-south-1": "AFS1"}


def products(service, filters):
    args = ["pricing", "get-products", "--region", "us-east-1", "--service-code", service, "--filters"]
    args += [f"Type=TERM_MATCH,Field={k},Value={v}" for k, v in filters.items()]
    ok, data, err = aws_try(*args)
    if not ok:
        print(f"  price list query failed ({service} {filters}): {err[:120]}")
        return []
    return [json.loads(p) if isinstance(p, str) else p for p in data.get("PriceList", [])]


def on_demand_usd(prods, usage_suffix=None):
    """(price, unit, usagetype, description) of the first product whose usagetype ends with usage_suffix."""
    for p in prods:
        ut = p["product"]["attributes"].get("usagetype", "")
        if usage_suffix and not ut.endswith(usage_suffix):
            continue
        for term in p.get("terms", {}).get("OnDemand", {}).values():
            for dim in term["priceDimensions"].values():
                usd = float(dim["pricePerUnit"].get("USD", 0))
                if usd > 0:
                    return {"usd": usd, "unit": dim["unit"], "usagetype": ut, "description": dim["description"][:90]}
    return None


def main():
    o = tf_outputs()
    sz = o["task_size"]
    vcpu, gb = sz["cpu"] / 1024, sz["memory"] / 1024
    itype = o["instance_type"]
    px = PREFIX.get(REGION, "USE1")
    p = {}
    for key, ut in {"fargate_vcpu": f"{px}-Fargate-vCPU-Hours:perCPU", "fargate_gb": f"{px}-Fargate-GB-Hours",
                    "fargate_arm_vcpu": f"{px}-Fargate-ARM-vCPU-Hours:perCPU", "fargate_arm_gb": f"{px}-Fargate-ARM-GB-Hours",
                    "spot_vcpu": f"{px}-SpotUsage-Fargate-vCPU-Hours:perCPU", "spot_gb": f"{px}-SpotUsage-Fargate-GB-Hours"}.items():
        p[key] = on_demand_usd(products("AmazonECS", {"usagetype": ut}))
    p["ec2"] = on_demand_usd(products("AmazonEC2", {"instanceType": itype, "regionCode": REGION, "operatingSystem": "Linux",
                                                    "tenancy": "Shared", "preInstalledSw": "NA", "capacitystatus": "Used"}),
                             f"BoxUsage:{itype}")
    p["gp3"] = on_demand_usd(products("AmazonEC2", {"regionCode": REGION, "volumeApiName": "gp3", "productFamily": "Storage"}))
    p["ipv4"] = on_demand_usd(products("AmazonVPC", {"usagetype": f"{px}-PublicIPv4:InUseAddress"}))

    eni = read_json("ec2-eni")
    if not eni:
        from ec2_capacity import eni_facts
        eni = eni_facts(itype, 256, 512, 6)
    # the comparison task is the routes-api size, so recompute CPU/memory limits for it
    by_eni = eni["tasks_per_instance_by_eni"]
    by_cpu = eni["vcpus"] * 1024 // sz["cpu"]
    by_mem = int(eni["memory_mib"] * 0.9) // sz["memory"]
    tpi = min(by_eni, by_cpu, by_mem)
    tpi_no_eni = min(by_cpu, by_mem)

    def fargate(vk, gk):
        if p[vk] and p[gk]:
            return round(1000 * (vcpu * p[vk]["usd"] + gb * p[gk]["usd"]), 2)
        return None

    ec2_hour = (p["ec2"]["usd"] if p["ec2"] else None)
    vol_hour = (30 * p["gp3"]["usd"] / 730) if p["gp3"] else 0.0
    ec2_cost = round(1000 / tpi * (ec2_hour + vol_hour), 2) if ec2_hour and tpi else None
    ec2_cost_no_eni = round(1000 / tpi_no_eni * (ec2_hour + vol_hour), 2) if ec2_hour and tpi_no_eni else None
    ip = p["ipv4"]["usd"] if p["ipv4"] else None
    out = {
        "region": REGION, "task": f"{vcpu:g} vCPU / {gb:g} GB", "prices": p,
        "per_1000_task_hours_usd": {
            "FARGATE on-demand (x86_64)": fargate("fargate_vcpu", "fargate_gb"),
            "FARGATE on-demand (ARM64, reference)": fargate("fargate_arm_vcpu", "fargate_arm_gb"),
            "FARGATE_SPOT": fargate("spot_vcpu", "spot_gb"),
            f"EC2 {itype} on-demand, {tpi} tasks/instance (ENI-limited)": ec2_cost,
            f"EC2 {itype} if {tpi_no_eni} tasks/instance fitted (no ENI limit)": ec2_cost_no_eni,
        },
        "public_ipv4_per_1000_task_hours_usd": {
            "Fargate (1 address per task)": round(1000 * ip, 2) if ip else None,
            f"EC2 (1 address per instance / {tpi} tasks)": round(1000 * ip / tpi, 2) if ip and tpi else None},
        "tasks_per_instance": {"by_eni": by_eni, "by_cpu": by_cpu, "by_memory_approx": by_mem, "used": tpi},
        "fargate_spot_in_price_list": bool(p["spot_vcpu"] and p["spot_gb"]),
        "notes": ["EC2 cost includes the 30 GB gp3 root volume; it assumes instances are fully packed (managed "
                  "scaling with target capacity 100 aims for that); t4g in standard credit mode.",
                  "Fargate Spot prices change with spare capacity: check Cost Explorer (usage types containing "
                  "SpotUsage-Fargate) the day after the build for what was actually charged."],
    }
    print(f"Price List API, {REGION}, task = {out['task']}")
    for k in ("fargate_vcpu", "fargate_gb", "spot_vcpu", "spot_gb", "ec2", "gp3", "ipv4"):
        v = p[k]
        print(f"  {k:<13} " + (f"US${v['usd']:.6f} per {v['unit']}  ({v['usagetype']})" if v else "not in the Price List API"))
    print("\nCost per 1,000 task-hours (US$):")
    for k, v in out["per_1000_task_hours_usd"].items():
        print(f"  {k:<58} {'%.2f' % v if v is not None else '-'}")
    if not out["fargate_spot_in_price_list"]:
        print("  (FARGATE_SPOT: no Fargate Spot product in the Price List API for this Region - see Cost Explorer tomorrow)")
    print("Public IPv4 on top (US$ per 1,000 task-hours):")
    for k, v in out["public_ipv4_per_1000_task_hours_usd"].items():
        print(f"  {k:<58} {'%.2f' % v if v is not None else '-'}")
    print(f"tasks per {itype}: by ENI {by_eni}, by CPU {by_cpu}, by memory ~{by_mem} -> {tpi}")
    print("saved", write_result("prices", out))


if __name__ == "__main__":
    main()
