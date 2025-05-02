#!/usr/bin/env python3
"""
get_aws_costs.py  –  pull last week's AWS spend, grouped by
                    SERVICE, LINKED_ACCOUNT, USAGE_TYPE, and TAG:project.
                    Writes one JSON + one PNG per grouping.
"""
import os, json, time, datetime
import boto3, pandas as pd, matplotlib.pyplot as plt

# ───────── CONFIG ─────────
PROFILE  = "cost-report"                           # your aws configure profile
TODAY    = datetime.datetime.now().strftime("%d-%m-%Y")
OUT_DIR  = os.path.join(os.getcwd(), f"aws-cost-reports-{TODAY}")
os.makedirs(OUT_DIR, exist_ok=True)

# Groupings you want – append more if needed
DIMENSIONS = [
    {"Type": "DIMENSION", "Key": "SERVICE"},
    {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
    {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
    {"Type": "TAG",       "Key": "Project"},   # <── new tag-based breakdown
]

THROTTLE = 1  # seconds between calls (Cost Explorer limit is 5 req/s)

# ───────── helpers ─────────
def last_week():
    today = datetime.date.today()
    end   = today 
    start = today - datetime.timedelta(days=7)
    return start.isoformat(), end.isoformat()

def ce_client():
    return boto3.Session(profile_name=PROFILE).client("ce")

def fetch(client, start, end, group):
    return client.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["AmortizedCost"],
        GroupBy=[group]
    )

def resp_to_df(resp):
    groups = resp["ResultsByTime"][0]["Groups"]
    keys   = [g["Keys"][0] for g in groups]
    costs  = [float(g["Metrics"]["AmortizedCost"]["Amount"]) for g in groups]
    return pd.DataFrame({"Key": keys, "Cost": costs}).sort_values("Cost", ascending=False)

def make_chart(df, title, path):
    plt.figure(figsize=(12,8))
    x = range(len(df))
    plt.bar(x, df["Cost"])
    plt.xticks(x, df["Key"], rotation=45, ha="right")
    plt.title(title, fontsize=18)
    plt.ylabel("USD")
    plt.tight_layout(pad=2.0)
    plt.savefig(path, bbox_inches="tight")
    plt.close()

# ───────── main ─────────
def main():
    start, end = last_week()
    client = ce_client()

    for g in DIMENSIONS:
        key = g["Key"]
        print(f"→ AWS grouping by {key} …")
        resp = fetch(client, start, end, g)

        # Save JSON data
        json_path = os.path.join(OUT_DIR, f"raw_{key}_{start}_{end}.json")
        with open(json_path, "w") as f:
            json.dump(resp, f, indent=2)
        print(f"💾  Saved JSON: {json_path}")

        df = resp_to_df(resp)

        # limit UsageType chart to top 20 cost buckets
        if g["Key"] == "USAGE_TYPE":
            df = df.head(20)

        p_path = os.path.join(OUT_DIR, f"{key}_{start}_{end}.png")
        make_chart(df, f"AWS {key} cost {start}→{end}", p_path)

        print(f"📊  {p_path}")

        time.sleep(THROTTLE)

if __name__ == "__main__":
    main()
