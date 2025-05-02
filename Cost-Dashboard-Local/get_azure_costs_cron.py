import os
import json
import time
import datetime
import argparse
import requests
import pandas as pd
import matplotlib.pyplot as plt
from requests.exceptions import HTTPError
from azure.identity import AzureCliCredential
import logging

# ────────── CONFIG ──────────
SUBSCRIPTION_ID   = "1cbd30d4-5a1f-4cb1-839f-5b8b66807c1d"
API_VERSION      = "2025-03-01"
OUTPUT_DIR       = os.path.join(os.getcwd(), "azure-cost-reports")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# throttling rules
SHORT_SLEEP      = 10    # seconds between calls
LONG_SLEEP       = 60    # seconds after every 3 calls
CALLS_BEFORE_LONG = 3    # how many calls to do before the long sleep

def get_last_week_range():
    today = datetime.date.today()
    end_date = today 
    start_date = today - datetime.timedelta(days=7)
    return start_date, end_date

def get_token():
    cred  = AzureCliCredential()
    token = cred.get_token("https://management.azure.com/.default").token
    return token

def query_cost(token, start, end, grouping):
    """
    Make one CostManagement/query call.
    Retry once on 429 after SHORT_SLEEP.
    """
    body = {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": { "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z" },
        "dataset": {
            "granularity": "None",
            "aggregation": {
                "totalCost": { "name": "PreTaxCost", "function": "Sum" }
            },
            "grouping": [ grouping ]
        }
    }
    url = (
        f"https://management.azure.com"
        f"/subscriptions/{SUBSCRIPTION_ID}"
        f"/providers/Microsoft.CostManagement/query"
        f"?api-version={API_VERSION}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json"
    }

    for attempt in (1, 2):
        resp = requests.post(url, headers=headers, json=body)
        if resp.status_code == 429 and attempt == 1:
            print(f"⚠️  429 on {grouping['name']}, backing off for {SHORT_SLEEP}s…")
            time.sleep(SHORT_SLEEP)
            continue
        try:
            resp.raise_for_status()
        except HTTPError:
            print(f"\n❌  ERROR for grouping '{grouping['name']}' →", resp.status_code)
            print(resp.text, "\n")
            raise
        return resp.json()

def query_project_by_region(token, start, end):
    """
    Query costs grouped by project tag and resource location.
    """
    body = {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": { "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z" },
        "dataset": {
            "granularity": "None",
            "aggregation": {
                "totalCost": { "name": "PreTaxCost", "function": "Sum" }
            },
            "grouping": [
                {"type": "TagKey", "name": "project"},
                {"type": "Dimension", "name": "ResourceLocation"}
            ]
        }
    }
    url = (
        f"https://management.azure.com"
        f"/subscriptions/{SUBSCRIPTION_ID}"
        f"/providers/Microsoft.CostManagement/query"
        f"?api-version={API_VERSION}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json"
    }
    
    logging.info(f"Querying project by region with body: {json.dumps(body)}")

    for attempt in (1, 2):
        resp = requests.post(url, headers=headers, json=body)
        if resp.status_code == 429 and attempt == 1:
            print(f"⚠️  429 on project by region query, backing off for {LONG_SLEEP}s…")
            time.sleep(LONG_SLEEP)
            continue
        try:
            resp.raise_for_status()
            logging.info("Successfully fetched project by region data")
            return resp.json()
        except HTTPError:
            logging.error(f"\n❌  ERROR for project by region query →", resp.status_code)
            logging.error(resp.text, "\n")
            if attempt == 1:
                print(f"Retrying project by region query after {LONG_SLEEP}s...")
                time.sleep(LONG_SLEEP)
            else:
                raise # Raise the error after the second failed attempt
        except Exception as e:
             logging.error(f"Unexpected error during project by region query: {str(e)}")
             raise

    # Fallback if loop finishes without returning/raising (shouldn't happen)
    logging.error("Query for project by region failed after retries.")
    return {
        "properties": {
            "columns": [
                {"name": "TagKey"}, {"name": "TagValue"}, {"name": "ResourceLocation"},
                {"name": "PreTaxCost"}, {"name": "Currency"}
            ],
            "rows": []
        }
    }

def query_project_by_resource(token, start, end):
    """
    Query costs grouped by project tag and resource ID.
    """
    body = {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": { "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z" },
        "dataset": {
            "granularity": "None",
            "aggregation": {
                "totalCost": { "name": "PreTaxCost", "function": "Sum" }
            },
            "grouping": [
                {"type": "TagKey", "name": "project"},
                {"type": "Dimension", "name": "ResourceId"}
            ]
        }
    }
    url = (
        f"https://management.azure.com"
        f"/subscriptions/{SUBSCRIPTION_ID}"
        f"/providers/Microsoft.CostManagement/query"
        f"?api-version={API_VERSION}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json"
    }
    
    logging.info(f"Querying project by resource with body: {json.dumps(body)}")

    for attempt in (1, 2):
        resp = requests.post(url, headers=headers, json=body)
        if resp.status_code == 429 and attempt == 1:
            print(f"⚠️  429 on project by resource query, backing off for {LONG_SLEEP}s…")
            time.sleep(LONG_SLEEP)
            continue
        try:
            resp.raise_for_status()
            logging.info("Successfully fetched project by resource data")
            return resp.json()
        except HTTPError:
            logging.error(f"\n❌  ERROR for project by resource query → {resp.status_code}")
            logging.error(resp.text)
            if attempt == 1:
                print(f"Retrying project by resource query after {LONG_SLEEP}s...")
                time.sleep(LONG_SLEEP)
            else:
                raise # Raise the error after the second failed attempt
        except Exception as e:
             logging.error(f"Unexpected error during project by resource query: {str(e)}")
             raise

    # Fallback if loop finishes without returning/raising (shouldn't happen)
    logging.error("Query for project by resource failed after retries.")
    return {
        "properties": {
            "columns": [
                {"name": "TagKey"}, {"name": "TagValue"}, {"name": "ResourceId"},
                {"name": "PreTaxCost"}, {"name": "Currency"}
            ],
            "rows": []
        }
    }

def get_current_billing_period(token):
    """
    Returns the open billing period for the subscription.

    Result:
        {"name": "202504-1",
         "start": "2025-04-01",
         "end":   "2025-05-01"}   # inclusive
    """
    url = (f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}"
           f"/providers/Microsoft.Billing/billingPeriods"
           "?api-version=2018-03-01-preview&$top=6")   # a few periods are plenty
    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    today = datetime.date.today()

    for period in resp.json().get("value", []):
        props = period.get("properties", {})
        start = datetime.datetime.strptime(
                    props["billingPeriodStartDate"], "%Y-%m-%d").date()
        # API's end date is *exclusive* – subtract one day to make it inclusive
        end_exclusive = datetime.datetime.strptime(
                    props["billingPeriodEndDate"], "%Y-%m-%d").date()
        end = end_exclusive - datetime.timedelta(days=1)

        if start <= today <= end:
            return {
                "name": period["name"],
                "start": start.isoformat(),
                "end":   end.isoformat()
            }

    return None 

def query_billing_cycle_total(token, start_date=None, end_date=None):
    """
    Query the total cost for the given date range.
    If start_date and end_date are provided, use them for a Custom timeframe.
    Otherwise, fall back to the BillingMonthToDate timeframe.
    """
    # Always query at the subscription scope
    url = (f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}"
           f"/providers/Microsoft.CostManagement/query"
           f"?api-version={API_VERSION}")

    if start_date and end_date:
        # Use the provided dates for the billing cycle
        body = {
            "type": "Usage",
            "timeframe": "Custom",
            "timePeriod": {
                "from": f"{start_date}T00:00:00Z",
                "to": f"{end_date}T23:59:59Z"
            },
            "dataset": {
                "granularity": "None",
                "aggregation": {"totalCost": {
                    "name": "PreTaxCost", "function": "Sum"}}
            }
        }
        print(f"ℹ️  Querying total cost for period: {start_date} to {end_date}")
    else:
        # Fallback to BillingMonthToDate if dates are not available
        print("⚠️  Billing period dates not found, falling back to BillingMonthToDate.")
        body = {
            "type": "Usage",
            "timeframe": "BillingMonthToDate",
            "dataset": {
                "granularity": "None",
                "aggregation": {"totalCost": {
                    "name": "PreTaxCost", "function": "Sum"}}
            }
        }

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    resp = requests.post(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()

    rows = resp.json()["properties"]["rows"]
    return {
        "total_cost": rows[0][0] if rows else 0,
        "currency":   rows[0][1] if rows else "USD"
    }

def json_to_df(j):
    props = j["properties"]
    cols  = [c["name"] for c in props["columns"]]
    rows  = props["rows"]
    return pd.DataFrame(rows, columns=cols)

def main(tag_keys):
    # ── 1.  Prep ────────────────────────────────────────────────────────────────
    start, end = get_last_week_range()
    token      = get_token()
    s_iso, e_iso = start.isoformat(), end.isoformat()

    # Dimensions to query
    dims = [
        {"type": "Dimension", "name": "ResourceGroupName"},
        {"type": "Dimension", "name": "MeterCategory"},
        {"type": "Dimension", "name": "MeterSubCategory"},
        {"type": "Dimension", "name": "ResourceType"},
        {"type": "TagKey",    "name": "project"},
        {"type": "Dimension", "name": "ResourceLocation"},
    ]
    for tag in tag_keys:
        if tag.lower() != "project":
            dims.append({"type": "TagKey", "name": tag})

    # ── 2.  Weekly grouping queries (unchanged) ────────────────────────────────
    for idx, g in enumerate(dims,  start=1):
        key = g["name"]
        print(f"\n→ Querying grouping by {key} …")
        j = query_cost(token, s_iso, e_iso, g)

        json_fn = os.path.join(OUTPUT_DIR, f"raw_{key}.json")
        with open(json_fn, "w") as f:
            json.dump(j, f, indent=2)
        print(f"💾  Wrote JSON: {json_fn}")

        if idx % CALLS_BEFORE_LONG == 0 and idx < len(dims):
            print(f"⏱  Done {CALLS_BEFORE_LONG} calls—sleeping {LONG_SLEEP}s…")
            time.sleep(LONG_SLEEP)
        elif idx < len(dims):
            print(f"⏱  Sleeping {SHORT_SLEEP}s before next query…")
            time.sleep(SHORT_SLEEP)

    # ── 3.  Determine current billing period (NEW) ─────────────────────────────
    period = None
    try:
        print("\n→ Determining current billing period …")
        time.sleep(SHORT_SLEEP)
        period = get_current_billing_period(token)   # ← new helper
        if period:
            json_fn = os.path.join(OUTPUT_DIR, "billing_cycle_dates.json")
            with open(json_fn, "w") as f:
                json.dump(period, f, indent=2)
            print(f"💾  Wrote JSON: {json_fn}")
            print(f"🗓️  Current billing period: {period['start']} → {period['end']}")
        else:
            print("⚠️  Could not determine current billing period – will fall back to Month-to-Date.")
    except Exception as e:
        print(f"⚠️  Error while fetching billing period: {e}")

    # ── 4.  Project costs by region ────────────────────────────────────────────
    try:
        print("\n→ Querying project costs by region …")
        print(f"⏱  Sleeping {LONG_SLEEP}s before project-by-region query …")
        time.sleep(LONG_SLEEP)

        j = query_project_by_region(token, s_iso, e_iso)
        json_fn = os.path.join(OUTPUT_DIR, "raw_project_by_region.json")
        with open(json_fn, "w") as f:
            json.dump(j, f, indent=2)
        print(f"💾  Wrote JSON: {json_fn}")
    except Exception as e:
        print(f"⚠️  Error during project-by-region query: {e}")

    # ── 5.  Project costs by resource ──────────────────────────────────────────
    try:
        print("\n→ Querying project costs by resource …")
        print(f"⏱  Sleeping {LONG_SLEEP}s before project-by-resource query …")
        time.sleep(LONG_SLEEP)

        j = query_project_by_resource(token, s_iso, e_iso)
        json_fn = os.path.join(OUTPUT_DIR, "raw_project_by_resource.json")
        with open(json_fn, "w") as f:
            json.dump(j, f, indent=2)
        print(f"💾  Wrote JSON: {json_fn}")
    except Exception as e:
        print(f"⚠️  Error during project-by-resource query: {e}")

    # ── 6.  Accurate billing-cycle total (NEW) ────────────────────────────────
    try:
        print("\n→ Querying billing-cycle total cost …")
        print(f"⏱  Sleeping {LONG_SLEEP}s before billing-cycle-total query …")
        time.sleep(LONG_SLEEP)

        # Pass start and end dates if the period was found
        start_billing = period["start"] if period else None
        end_billing = period["end"] if period else None
        billing_total = query_billing_cycle_total(token,
                                                  start_date=start_billing,
                                                  end_date=end_billing)

        json_fn = os.path.join(OUTPUT_DIR, "billing_cycle_total.json")
        with open(json_fn, "w") as f:
            json.dump(billing_total, f, indent=2)
        print(f"💾  Wrote JSON: {json_fn}")
        print(f"✅ Billing-cycle total so far: "
              f"{billing_total['currency']} {billing_total['total_cost']:.2f}")
    except Exception as e:
        print(f"⚠️  Error during billing-cycle-total query: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Weekly Azure Cost Report + Charts"
    )
    parser.add_argument(
        "--tags", "-t", nargs="*",
        default=[],
        help="Tag keys to group by (e.g. Environment CostCenter)"
    )
    args = parser.parse_args()
    main(args.tags)