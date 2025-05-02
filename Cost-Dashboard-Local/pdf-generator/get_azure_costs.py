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

# ────────── CONFIG ──────────
SUBSCRIPTION_ID   = "1cbd30d4-5a1f-4cb1-839f-5b8b66807c1d"
API_VERSION      = "2025-03-01"
OUTPUT_DIR       = os.path.join(os.getcwd(), f"azure-cost-reports-{datetime.datetime.now().strftime('%d-%m-%Y')}")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# throttling rules
SHORT_SLEEP      = 10    # seconds between calls
LONG_SLEEP       = 60    # seconds after every 4 calls
CALLS_BEFORE_LONG = 4    # how many calls to do before the long sleep

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

def json_to_df(j):
    props = j["properties"]
    cols  = [c["name"] for c in props["columns"]]
    rows  = props["rows"]
    return pd.DataFrame(rows, columns=cols)

def plot_and_save(df, group_key, start, end):
    # Identify the cost column (ends with 'cost', case-insensitive)
    cost_col = [c for c in df.columns if c.lower().endswith("cost")][0]
    
    # Special case for TagKey/TagValue response format when querying by tag
    if "TagKey" in df.columns and "TagValue" in df.columns:
        # Check if this is the tag we're looking for
        unique_tag_keys = df["TagKey"].unique()
        if len(unique_tag_keys) == 1 and unique_tag_keys[0].lower() == group_key.lower():
            print(f"ℹ️  Found tag grouping response format. Using 'TagValue' column for '{group_key}' values")
            actual_group_col = "TagValue"
        else:
            print(f"⚠️  Found TagKey/TagValue columns but with unexpected values: {unique_tag_keys}")
            raise ValueError(f"Unexpected TagKey values when querying for '{group_key}'")
    else:
        # Original logic for non-tag dimensions
        potential_group_cols = [
            c for c in df.columns 
            if c.lower() != cost_col.lower() and c.lower() != "currency"
        ]

        if len(potential_group_cols) == 1:
            # If we found exactly one other column, assume it's the grouping column
            actual_group_col = potential_group_cols[0]
            print(f"ℹ️  Identified grouping column as '{actual_group_col}' for group_key '{group_key}'")
        elif group_key in df.columns:
            # Fallback: If the originally expected group_key exists as a column, use it.
            actual_group_col = group_key
            print(f"⚠️  Could not definitively identify grouping column by elimination, falling back to checking for exact match '{group_key}'")
        else:
            # If we can't find it by elimination or exact match, raise a more informative error.
            raise ValueError(f"Could not find the grouping column for group_key '{group_key}' in DataFrame columns: {list(df.columns)}. Check the raw JSON file.")

    # Use actual_group_col for sorting and plotting labels
    df_sorted = df.sort_values(by=cost_col, ascending=False).head(20)

    # Handle null values in the group column (often shown as "Untagged" in Azure portal)
    if actual_group_col in df_sorted.columns:
        df_sorted[actual_group_col] = df_sorted[actual_group_col].fillna("Untagged")
    
    # Create a larger figure size by default
    plt.figure(figsize=(12, 8))
    
    # Create numeric x positions for the bars
    x_pos = range(len(df_sorted))
    
    # Plot the bar chart using numeric positions
    plt.bar(x_pos, df_sorted[cost_col])
    
    # Set the x-tick positions and use labels from the identified actual_group_col
    plt.xticks(x_pos, df_sorted[actual_group_col], rotation=45, ha="right") 
    
    # Add title (using the original group_key) and labels
    plt.title(f"Azure {group_key} cost, {start}→{end}", fontsize=20) # Title still uses the intended group_key name
    plt.ylabel(cost_col)
    
    # Add some padding at the bottom for the rotated labels
    plt.tight_layout(pad=3.0)
    plt.subplots_adjust(bottom=0.25)  # Add more bottom margin for labels

    # Save the figure (using the original group_key for the filename)
    fn = f"{group_key}_{start}_{end}.png".replace(" ", "_") # Filename uses intended group_key
    outpath = os.path.join(OUTPUT_DIR, fn)
    plt.savefig(outpath, bbox_inches='tight')  # Add tight bounding box
    plt.close()
    print(f"📊  Saved chart: {outpath}")

def main(tag_keys):
    start, end = get_last_week_range()
    token      = get_token()
    s_iso, e_iso = start.isoformat(), end.isoformat()

    # Dimensions to query
    dims = [
        {"type":"Dimension", "name":"ResourceGroupName"},
        {"type":"Dimension", "name":"MeterCategory"},
        {"type":"Dimension", "name":"MeterSubCategory"},
        {"type":"Dimension", "name":"ResourceType"},
        {"type":"TagKey", "name":"project"}, # Added TagKey for project directly
        # {"type":"Dimension", "name":"ResourceId"},
    ]
    # You might keep this loop if you want to add *other* tags via command line as well
    for tag in tag_keys:
        # Optional: prevent adding 'project' twice if passed via command line
        if tag.lower() != 'project':
             dims.append({"type":"TagKey", "name": tag})

    for idx, g in enumerate(dims, start=1):
        key = g["name"]
        print(f"\n→ Querying grouping by {key} …")
        j = query_cost(token, s_iso, e_iso, g)

        # save raw JSON
        json_fn = os.path.join(OUTPUT_DIR, f"raw_{key}_{s_iso}_{e_iso}.json")
        with open(json_fn, "w") as f:
            json.dump(j, f, indent=2)
        print(f"💾  Wrote JSON: {json_fn}")

        # build DataFrame & chart
        df = json_to_df(j).rename(columns={"totalCost":"PreTaxCost"})
        plot_and_save(df, key, s_iso, e_iso)

        # throttle: after every CALLS_BEFORE_LONG calls, sleep LONG_SLEEP; otherwise SHORT_SLEEP
        if idx % CALLS_BEFORE_LONG == 0 and idx < len(dims):
            print(f"⏱  Done {CALLS_BEFORE_LONG} calls—sleeping {LONG_SLEEP}s…")
            time.sleep(LONG_SLEEP)
        elif idx < len(dims):
            print(f"⏱  Sleeping {SHORT_SLEEP}s before next query…")
            time.sleep(SHORT_SLEEP)

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



