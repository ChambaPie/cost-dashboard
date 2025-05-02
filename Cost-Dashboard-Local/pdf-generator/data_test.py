import os
import json
import datetime

# Get today's date format for the directory
today = datetime.datetime.now().strftime("%d-%m-%Y")
aws_dir = f"aws-cost-reports-{today}"

if not os.path.exists(aws_dir):
    print(f"Directory {aws_dir} not found!")
    exit(1)

print(f"Processing JSON files in {aws_dir}...")

# Find all raw JSON files
json_files = [f for f in os.listdir(aws_dir) if f.startswith("raw_") and f.endswith(".json")]
print(f"Found {len(json_files)} JSON files")

totals = {}

# Process each file
for filename in sorted(json_files):
    filepath = os.path.join(aws_dir, filename)
    with open(filepath) as f:
        data = json.load(f)
    
    # Extract metric and grouping type from filename
    metric_name = filename.split("_")[1]
    
    # Initialize total for this file
    file_total = 0
    
    # Sum up all costs in this file
    for time_result in data.get("ResultsByTime", []):
        # Sum groups if they exist
        groups = time_result.get("Groups", [])
        if groups:
            for group in groups:
                cost = float(group["Metrics"]["AmortizedCost"]["Amount"])
                file_total += cost
        # If no groups, look for a Total field
        elif "Total" in time_result:
            cost = float(time_result["Total"]["AmortizedCost"]["Amount"])
            file_total += cost
    
    totals[metric_name] = file_total
    print(f"{metric_name:15s}: ${file_total:.2f}")

# Calculate the grand total across all files
grand_total = sum(totals.values())
print("\nUnique totals found:")
unique_totals = set(totals.values())
for total in sorted(unique_totals):
    print(f"${total:.2f}")

print(f"\nSum of all file totals: ${grand_total:.2f}")

# Check if we need to divide by the number of files to get the actual total
if len(unique_totals) == 1:
    print(f"All files show the same total: ${next(iter(unique_totals)):.2f}")
    print("The discrepancy is likely between the AWS API data and the UI display.")
else:
    print("Files show different totals. The issue might be in how the data is grouped.")