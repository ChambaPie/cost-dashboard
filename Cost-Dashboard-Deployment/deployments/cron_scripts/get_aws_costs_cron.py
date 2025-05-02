#!/usr/bin/env python3
"""
get_aws_costs.py  –  pull last week's AWS spend, grouped by
                    various dimensions and tags, similar to the Azure cost script.
                    Writes JSON files to MinIO for each grouping and the billing period total.
"""
import os, json, time, datetime
import boto3, pandas as pd
import logging
import s3fs  # Added for MinIO support

from configuration import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET

# ───────── CONFIG ─────────
PROFILE  = "cost-report"                           # your aws configure profile
TODAY    = datetime.datetime.now().strftime("%d-%m-%Y")

# Output directory within MinIO bucket
OUT_DIR = f"{MINIO_BUCKET}/aws-cost-reports"

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Single dimension groupings
DIMENSIONS = [
    {"Type": "DIMENSION", "Key": "SERVICE"},
    {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
    {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
    {"Type": "DIMENSION", "Key": "OPERATION"},
    {"Type": "DIMENSION", "Key": "REGION"},
    {"Type": "DIMENSION", "Key": "INSTANCE_TYPE"},
    {"Type": "DIMENSION", "Key": "PLATFORM"},
    {"Type": "TAG",       "Key": "Project"},
]

# Throttling settings
THROTTLE = 1  # seconds between calls (Cost Explorer limit is 5 req/s)
LONG_THROTTLE = 3  # seconds between multi-dimension calls

# ───────── helpers ─────────
def last_week():
    today = datetime.date.today()
    end   = today 
    start = today - datetime.timedelta(days=7)
    return start.isoformat(), end.isoformat()

def ce_client():
    # Check for env vars (used in Kubernetes deployment)
    if "AWS_ACCESS_KEY_ID" in os.environ and "AWS_SECRET_ACCESS_KEY" in os.environ:
        logging.info("Using AWS credentials from environment variables")
        return boto3.client("ce",
                           aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                           aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                           region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    # Fallback to profile for local development
    logging.info(f"Using AWS credentials from profile: {PROFILE}")
    return boto3.Session(profile_name=PROFILE).client("ce")

def init_minio():
    """Initialize MinIO connection"""
    logging.info("Initializing MinIO connection")
    return s3fs.S3FileSystem(
        endpoint_url=os.environ.get("MINIO_ENDPOINT", MINIO_ENDPOINT),
        key=os.environ.get("MINIO_ACCESS_KEY", MINIO_ACCESS_KEY),
        secret=os.environ.get("MINIO_SECRET_KEY", MINIO_SECRET_KEY),
        use_ssl=True,
        client_kwargs={'verify': False}
    )

def fetch(client, start, end, groups):
    """
    Fetch cost data grouped by one or more dimensions/tags.
    If groups is a single group, it's treated as-is.
    If groups is a list, multiple dimensions are used for grouping.
    """
    if not isinstance(groups, list):
        groups = [groups]
    
    try:
        logging.info(f"Fetching costs for {[g['Key'] for g in groups]}")
        return client.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity="MONTHLY",
            Metrics=["AmortizedCost"],
            GroupBy=groups
        )
    except Exception as e:
        logging.error(f"Error fetching costs: {str(e)}")
        # Return empty result structure
        return {
            "ResultsByTime": [{
                "TimePeriod": {"Start": start, "End": end},
                "Groups": [],
                "Estimated": True
            }]
        }

def resp_to_df(resp):
    """
    Convert API response to DataFrame.
    Handles both single and multi-dimension groupings.
    """
    groups = resp["ResultsByTime"][0]["Groups"]
    if not groups:
        return pd.DataFrame({"Key": [], "Cost": []})
    
    # For multi-dimension grouping, keys will be a list of values
    multi_dim = len(groups[0]["Keys"]) > 1 if groups else False
    
    if multi_dim:
        # For multi-dimension, we'll create columns for each dimension
        data = []
        for g in groups:
            item = {f"Dim{i+1}": val for i, val in enumerate(g["Keys"])}
            item["Cost"] = float(g["Metrics"]["AmortizedCost"]["Amount"])
            data.append(item)
        return pd.DataFrame(data).sort_values("Cost", ascending=False)
    else:
        # Single dimension (original behavior)
        keys = [g["Keys"][0] for g in groups]
        costs = [float(g["Metrics"]["AmortizedCost"]["Amount"]) for g in groups]
        return pd.DataFrame({"Key": keys, "Cost": costs}).sort_values("Cost", ascending=False)

def get_billing_period(client):
    """
    Get the current AWS billing period (month).
    Returns a dict with start and end dates.
    """
    try:
        today = datetime.date.today()
        first_of_month = today.replace(day=1)
        
        # AWS billing periods are calendar months
        if today.month == 12:
            next_month = datetime.date(today.year + 1, 1, 1)
        else:
            next_month = datetime.date(today.year, today.month + 1, 1)
            
        return {
            "start": first_of_month.isoformat(),
            "end": (next_month - datetime.timedelta(days=1)).isoformat()
        }
    except Exception as e:
        logging.error(f"Error determining billing period: {str(e)}")
        return None

def get_billing_cycle_total(client, start_date=None, end_date=None):
    """
    Get the total cost for the current billing cycle.
    If start_date and end_date are provided, use them.
    Otherwise, uses the current calendar month.
    """
    try:
        if not start_date or not end_date:
            period = get_billing_period(client)
            if not period:
                logging.error("Could not determine billing period")
                return {"total_cost": 0, "currency": "USD"}
            start_date = period["start"]
            end_date = period["end"]
            
        logging.info(f"Getting billing cycle total for {start_date} to {end_date}")
        
        response = client.get_cost_and_usage(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="MONTHLY",
            Metrics=["AmortizedCost"]
        )
        
        if not response["ResultsByTime"]:
            return {"total_cost": 0, "currency": "USD"}
            
        total = float(response["ResultsByTime"][0]["Total"]["AmortizedCost"]["Amount"])
        currency = response["ResultsByTime"][0]["Total"]["AmortizedCost"]["Unit"]
        
        return {"total_cost": total, "currency": currency}
    except Exception as e:
        logging.error(f"Error fetching billing cycle total: {str(e)}")
        return {"total_cost": 0, "currency": "USD"}

# ───────── main ─────────
def main():
    start, end = last_week()
    client = ce_client()
    fs = init_minio()  # Initialize MinIO

    # Ensure the AWS cost reports directory exists in MinIO
    try:
        if not fs.exists(OUT_DIR):
            logging.info(f"Creating directory: {OUT_DIR}")
            fs.mkdir(OUT_DIR)
    except Exception as e:
        logging.error(f"Error creating directory in MinIO: {str(e)}")

    # 1. Single dimension queries
    for g in DIMENSIONS:
        key = g["Key"]
        print(f"→ AWS grouping by {key} …")
        resp = fetch(client, start, end, g)

        # Save JSON data to MinIO
        json_path = f"{OUT_DIR}/raw_{key}.json"
        try:
            with fs.open(json_path, "w") as f:
                json.dump(resp, f, indent=2)
            print(f"💾  Saved JSON to MinIO: {json_path}")
        except Exception as e:
            logging.error(f"Error saving to MinIO: {str(e)}")
            
        df = resp_to_df(resp)

        # limit UsageType chart to top 20 cost buckets
        if g["Key"] == "USAGE_TYPE":
            df = df.head(20)

        time.sleep(THROTTLE)
    
    # 2. Project by Region query (similar to Azure's project by region)
    print("\n→ Querying Project tag by Region costs …")
    time.sleep(LONG_THROTTLE)
    
    project_region_groups = [
        {"Type": "TAG", "Key": "Project"},
        {"Type": "DIMENSION", "Key": "REGION"}
    ]
    
    resp = fetch(client, start, end, project_region_groups)
    json_path = f"{OUT_DIR}/raw_project_by_region.json"
    try:
        with fs.open(json_path, "w") as f:
            json.dump(resp, f, indent=2)
        print(f"💾  Saved JSON to MinIO: {json_path}")
    except Exception as e:
        logging.error(f"Error saving to MinIO: {str(e)}")
    
    # 3. Project by Resource query (using RESOURCE_ID dimension if available)
    # Note: AWS Cost Explorer doesn't directly expose RESOURCE_ID as a dimension
    # You can use a resource-id tag if you've set one up
    print("\n→ Querying Project tag by Resource costs …")
    time.sleep(LONG_THROTTLE)
    
    # First try with resource-id tag if you have one
    project_resource_groups = [
        {"Type": "TAG", "Key": "Project"},
        {"Type": "DIMENSION", "Key": "USAGE_TYPE"}  # Change this to match your tagging strategy
    ]
    
    resp = fetch(client, start, end, project_resource_groups)
    json_path = f"{OUT_DIR}/raw_project_by_resource.json"
    try:
        with fs.open(json_path, "w") as f:
            json.dump(resp, f, indent=2)
        print(f"💾  Saved JSON to MinIO: {json_path}")
    except Exception as e:
        logging.error(f"Error saving to MinIO: {str(e)}")
    
    # 4. Get billing cycle total (similar to Azure)
    print("\n→ Querying billing-cycle total cost …")
    time.sleep(LONG_THROTTLE)
    
    period = get_billing_period(client)
    billing_total = get_billing_cycle_total(client, 
                                         start_date=period["start"] if period else None,
                                         end_date=period["end"] if period else None)
    
    json_path = f"{OUT_DIR}/billing_cycle_total.json"
    try:
        with fs.open(json_path, "w") as f:
            json.dump(billing_total, f, indent=2)
        print(f"💾  Saved JSON to MinIO: {json_path}")
        print(f"✅ Billing-cycle total so far: {billing_total['currency']} {billing_total['total_cost']:.2f}")
    except Exception as e:
        logging.error(f"Error saving to MinIO: {str(e)}")

if __name__ == "__main__":
    main()