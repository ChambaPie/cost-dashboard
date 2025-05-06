import streamlit as st
import os
import json
import datetime
import pandas as pd
import matplotlib.pyplot as plt
import glob
import io
from fpdf import FPDF
import base64
import tempfile
from forex_python.converter import CurrencyRates
from currency_converter import CurrencyConverter, ECB_URL
import s3fs

# MinIO connection settings
from configuration import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET

# Page configuration
st.set_page_config(
    page_title="Cloud Cost Report",
    page_icon="💰",
    layout="wide"
)

# Add custom CSS for larger table fonts
st.markdown("""
<style>
    .stDataFrame table {
        font-size: 28px !important;
        width: 100% !important;
    }
    .stDataFrame th {
        font-size: 30px !important;
        font-weight: bold !important;
        background-color: #f0f2f6 !important;
    }
    .stDataFrame td {
        font-size: 28px !important;
    }
    /* Prevent horizontal scrolling */
    .stDataFrame {
        width: 100% !important;
        overflow-x: visible !important;
    }
    /* Style the download button */
    .download-button {
        background-color: #4CAF50;
        color: white;
        padding: 10px 15px;
        text-align: center;
        text-decoration: none;
        display: inline-block;
        font-size: 16px;
        margin: 4px 2px;
        cursor: pointer;
        border-radius: 4px;
    }
</style>
""", unsafe_allow_html=True)

# Hardcoded configuration values
# Date range (last 7 days)
today = datetime.date.today()
end_date = today 
start_date = today - datetime.timedelta(days=7)
start_iso = start_date.isoformat()
end_iso = end_date.isoformat()

# App title
st.title("Cloud Cost Report")
st.subheader(f"{start_date} to {end_date}")

# ----- AWS Cost Functions -----
def get_aws_costs_from_files():
    """Read AWS cost data from MinIO bucket"""
    try:
        aws_data = {
            "total": None,
            "service": None,
            "project": None,
            "billing_cycle": None,
            "project_by_region": None,
            "project_by_resource": None
        }
        
        # Initialize S3 filesystem connection to MinIO
        fs = s3fs.S3FileSystem(
            endpoint_url=os.environ.get("MINIO_ENDPOINT", MINIO_ENDPOINT),
            key=os.environ.get("MINIO_ACCESS_KEY", MINIO_ACCESS_KEY),
            secret=os.environ.get("MINIO_SECRET_KEY", MINIO_SECRET_KEY),
            use_ssl=True,
            client_kwargs={'verify': False},
            use_listings_cache=False,  # Disable cache
            skip_instance_cache=True   # Skip instance cache
        )
        
        # Path to AWS cost reports directory in MinIO bucket
        aws_dir = f"{MINIO_BUCKET}/aws-cost-reports"
        
        # Log available files for debugging
        all_aws_files = fs.glob(f"{aws_dir}/*.json")
        if not all_aws_files:
            st.warning(f"No JSON files found in {aws_dir} directory")
            # Try looking in root of bucket
            all_aws_files = fs.glob(f"{MINIO_BUCKET}/*.json")
            if all_aws_files:
                st.info(f"Found JSON files in bucket root instead: {all_aws_files}")
                aws_dir = MINIO_BUCKET
        
        # Find all relevant files with broader patterns
        service_files = fs.glob(f"{aws_dir}/*SERVICE*.json") + fs.glob(f"{aws_dir}/*service*.json")
        project_files = fs.glob(f"{aws_dir}/*Project*.json") + fs.glob(f"{aws_dir}/*project*.json")
        account_files = fs.glob(f"{aws_dir}/*LINKED_ACCOUNT*.json") + fs.glob(f"{aws_dir}/*account*.json")
        billing_cycle_files = fs.glob(f"{aws_dir}/*billing_cycle_total*.json")
        project_by_region_files = fs.glob(f"{aws_dir}/*project_by_region*.json")
        project_by_resource_files = fs.glob(f"{aws_dir}/*project_by_resource*.json")
        
        # Load SERVICE data (for total and service costs)
        if service_files:
            # Use the most recent file
            service_file = max(service_files, key=lambda x: fs.info(x)['LastModified'])
            with fs.open(service_file, 'r') as f:
                aws_data["service"] = json.load(f)
                # Service data can also be used for total costs
                aws_data["total"] = aws_data["service"]  # They have the same structure
        elif all_aws_files:
            # If no service file found, try using any available AWS file
            aws_file = max(all_aws_files, key=lambda x: fs.info(x)['LastModified'])
            with fs.open(aws_file, 'r') as f:
                data = json.load(f)
                # Check if this has the right structure
                if "ResultsByTime" in data:
                    aws_data["total"] = data
                    aws_data["service"] = data
        
        # Load Project data
        if project_files:
            # Use the most recent file
            project_file = max(project_files, key=lambda x: fs.info(x)['LastModified'])
            with fs.open(project_file, 'r') as f:
                aws_data["project"] = json.load(f)
        
        # Load Account data (if available)
        if account_files:
            account_file = max(account_files, key=lambda x: fs.info(x)['LastModified'])
            with fs.open(account_file, 'r') as f:
                aws_data["account"] = json.load(f)
        
        # Load billing cycle total
        if billing_cycle_files:
            billing_cycle_file = max(billing_cycle_files, key=lambda x: fs.info(x)['LastModified'])
            with fs.open(billing_cycle_file, 'r') as f:
                aws_data["billing_cycle"] = json.load(f)
        
        # Load project by region data
        if project_by_region_files:
            project_by_region_file = max(project_by_region_files, key=lambda x: fs.info(x)['LastModified'])
            with fs.open(project_by_region_file, 'r') as f:
                aws_data["project_by_region"] = json.load(f)
        
        # Load project by resource data
        if project_by_resource_files:
            project_by_resource_file = max(project_by_resource_files, key=lambda x: fs.info(x)['LastModified'])
            with fs.open(project_by_resource_file, 'r') as f:
                aws_data["project_by_resource"] = json.load(f)
        
        return aws_data
    except Exception as e:
        st.error(f"Error reading AWS cost data from MinIO: {str(e)}")
        return None

def process_aws_data(aws_data, metric="AmortizedCost"):
    """Process AWS cost data for display"""
    if not aws_data:
        return None, None, None
    
    # Process total costs by day (if available)
    daily_df = None
    if aws_data["total"]:
        try:
            # Raw AWS data from the files
            daily_totals = []
            total_cost = 0
            
            # Handle case where there are groups instead of direct Total
            if "ResultsByTime" in aws_data["total"]:
                # Create a sum of all service costs as the total
                service_costs = {}
                
                for time_result in aws_data["total"]["ResultsByTime"]:
                    day = time_result["TimePeriod"]["Start"]
                    daily_cost = 0
                    
                    # Try to extract from Groups
                    if "Groups" in time_result:
                        for group in time_result["Groups"]:
                            if "Metrics" in group and "AmortizedCost" in group["Metrics"]:
                                daily_cost += float(group["Metrics"]["AmortizedCost"]["Amount"])
                            elif isinstance(group, dict):
                                # Look for any cost metric
                                for key, value in group.items():
                                    if isinstance(value, dict) and "Amount" in value:
                                        daily_cost += float(value["Amount"])
                                        break
                    
                    # Try to extract from Total
                    elif "Total" in time_result:
                        if "AmortizedCost" in time_result["Total"]:
                            daily_cost = float(time_result["Total"]["AmortizedCost"]["Amount"])
                        elif isinstance(time_result["Total"], dict):
                            # Look for any cost metric
                            for key, value in time_result["Total"].items():
                                if isinstance(value, dict) and "Amount" in value:
                                    daily_cost = float(value["Amount"])
                                    break
                    
                    daily_totals.append({"Date": day, "Cost": daily_cost})
                    total_cost += daily_cost
                    
            if daily_totals:
                daily_df = pd.DataFrame(daily_totals)
            
            # If we still have 0 total, try calculating from the service data
            if total_cost == 0 and "service" in aws_data and aws_data["service"]:
                # Try extracting from raw data
                raw_service_cost = extract_cost_from_service_data(aws_data["service"])
                if raw_service_cost > 0:
                    # If we have service costs but no daily costs, create a simple daily DF
                    if daily_df is None or (isinstance(daily_df, pd.DataFrame) and daily_df["Cost"].sum() == 0):
                        # Create a single day entry with the total cost
                        daily_df = pd.DataFrame([{"Date": start_iso, "Cost": raw_service_cost}])
                
            # Another fallback: if we have an account data structure, try that
            if (daily_df is None or (isinstance(daily_df, pd.DataFrame) and daily_df["Cost"].sum() == 0)) and "account" in aws_data and aws_data["account"]:
                account_cost = extract_cost_from_account_data(aws_data["account"])
                if account_cost > 0:
                    # Create a single day entry with the total cost
                    daily_df = pd.DataFrame([{"Date": start_iso, "Cost": account_cost}])
        
        except Exception as e:
            st.warning(f"Error processing AWS daily costs: {str(e)}")
    
    # Process service costs
    service_df = None
    if aws_data["service"]:
        try:
            service_data = []
            
            # Extract costs from the service data
            for time_result in aws_data["service"]["ResultsByTime"]:
                if "Groups" in time_result:
                    for group in time_result["Groups"]:
                        service = group["Keys"][0]
                        cost = 0
                        
                        # Try various ways to extract the cost
                        if "Metrics" in group and "AmortizedCost" in group["Metrics"]:
                            cost = float(group["Metrics"]["AmortizedCost"]["Amount"])
                        elif "AmortizedCost" in group:
                            cost = float(group["AmortizedCost"]["Amount"])
                        else:
                            # Try to find any cost metric
                            for key, value in group.items():
                                if isinstance(value, dict) and "Amount" in value:
                                    cost = float(value["Amount"])
                                    break
                        
                        service_data.append({"Service": service, "Cost": cost})
            
            if service_data:
                service_df = pd.DataFrame(service_data).groupby("Service").sum().reset_index()
                service_df = service_df.sort_values("Cost", ascending=False)
                
                # If we got service data but no daily data, create daily from service total
                if service_df is not None and (daily_df is None or (isinstance(daily_df, pd.DataFrame) and daily_df["Cost"].sum() == 0)):
                    service_total = service_df["Cost"].sum()
                    if service_total > 0:
                        daily_df = pd.DataFrame([{"Date": start_iso, "Cost": service_total}])
        except Exception as e:
            st.warning(f"Error processing AWS service costs: {str(e)}")
    
    # Process account data if daily is still missing
    if (daily_df is None or (isinstance(daily_df, pd.DataFrame) and daily_df["Cost"].sum() == 0)) and "account" in aws_data and aws_data["account"]:
        try:
            account_data = []
            total_account_cost = 0
            
            # Extract costs from account data
            for time_result in aws_data["account"].get("ResultsByTime", []):
                if "Groups" in time_result:
                    for group in time_result["Groups"]:
                        account = group["Keys"][0]
                        cost = 0
                        
                        # Try various ways to extract the cost
                        if "Metrics" in group and "AmortizedCost" in group["Metrics"]:
                            cost = float(group["Metrics"]["AmortizedCost"]["Amount"])
                        elif "AmortizedCost" in group:
                            cost = float(group["AmortizedCost"]["Amount"])
                        else:
                            # Try to find any cost metric
                            for key, value in group.items():
                                if isinstance(value, dict) and "Amount" in value:
                                    cost = float(value["Amount"])
                                    break
                        
                        account_data.append({"Account": account, "Cost": cost})
                        total_account_cost += cost
            
            # Create a daily df from account total
            if total_account_cost > 0:
                daily_df = pd.DataFrame([{"Date": start_iso, "Cost": total_account_cost}])
        except Exception as e:
            st.warning(f"Error processing AWS account costs: {str(e)}")
    
    # Process project costs
    project_df = None
    if aws_data["project"]:
        try:
            project_data = []
            
            for time_result in aws_data["project"]["ResultsByTime"]:
                if "Groups" in time_result:
                    for group in time_result["Groups"]:
                        # Remove 'Project$' prefix if present
                        project = group["Keys"][0].replace("Project$", "")
                        cost = 0
                        
                        # Try various ways to extract the cost
                        if "Metrics" in group and "AmortizedCost" in group["Metrics"]:
                            cost = float(group["Metrics"]["AmortizedCost"]["Amount"])
                        elif "AmortizedCost" in group:
                            cost = float(group["AmortizedCost"]["Amount"])
                        else:
                            # Try to find any cost metric
                            for key, value in group.items():
                                if isinstance(value, dict) and "Amount" in value:
                                    cost = float(value["Amount"])
                                    break
                        
                        project_data.append({"Project": project, "Cost": cost})
            
            if project_data:
                project_df = pd.DataFrame(project_data).groupby("Project").sum().reset_index()
                project_df = project_df.sort_values("Cost", ascending=False)
                
                # If we got project data but no daily data, create daily from project total
                if project_df is not None and (daily_df is None or (isinstance(daily_df, pd.DataFrame) and daily_df["Cost"].sum() == 0)):
                    project_total = project_df["Cost"].sum()
                    if project_total > 0:
                        daily_df = pd.DataFrame([{"Date": start_iso, "Cost": project_total}])
        except Exception as e:
            st.warning(f"Error processing AWS project costs: {str(e)}")
    
    return daily_df, service_df, project_df

def extract_cost_from_account_data(account_data):
    """Extract total cost from account data structure"""
    total_cost = 0
    try:
        # Look for groups in the most recent time period
        if "ResultsByTime" in account_data and len(account_data["ResultsByTime"]) > 0:
            time_result = account_data["ResultsByTime"][0]  # Get the most recent time period
            
            if "Groups" in time_result:
                for group in time_result["Groups"]:
                    # Try to extract cost from metrics
                    if "Metrics" in group and "AmortizedCost" in group["Metrics"]:
                        total_cost += float(group["Metrics"]["AmortizedCost"]["Amount"])
                    # Look for any cost-like field
                    else:
                        for key, value in group.items():
                            if isinstance(value, dict) and "Amount" in value:
                                total_cost += float(value["Amount"])
                                break
    except Exception as e:
        st.warning(f"Error extracting costs from account data: {str(e)}")
    
    return total_cost

def extract_cost_from_service_data(service_data):
    """Extract total cost from service data structure"""
    total_cost = 0
    try:
        # Look for groups in the most recent time period
        if "ResultsByTime" in service_data and len(service_data["ResultsByTime"]) > 0:
            time_result = service_data["ResultsByTime"][0]  # Get the most recent time period
            
            if "Groups" in time_result:
                for group in time_result["Groups"]:
                    # Try to extract cost from metrics
                    if "Metrics" in group and "AmortizedCost" in group["Metrics"]:
                        total_cost += float(group["Metrics"]["AmortizedCost"]["Amount"])
                    # Look for any cost-like field
                    else:
                        for key, value in group.items():
                            if isinstance(value, dict) and "Amount" in value:
                                total_cost += float(value["Amount"])
                                break
    except Exception as e:
        st.warning(f"Error extracting costs from service data: {str(e)}")
    
    return total_cost

def process_aws_region_data(aws_data):
    """Process AWS regional cost data"""
    if not aws_data or "project_by_region" not in aws_data or not aws_data["project_by_region"]:
        return None, None, None
    
    try:
        # Process the region data grouped by project
        region_data = aws_data["project_by_region"]
        
        # Extract all regions and their costs
        all_regions_data = []
        untagged_regions_data = []
        
        # Process the data based on AWS Cost Explorer response structure
        for time_result in region_data.get("ResultsByTime", []):
            if "Groups" in time_result:
                for group in time_result["Groups"]:
                    # Keys are typically [Project, Region]
                    if len(group["Keys"]) >= 2:
                        project = group["Keys"][0].replace("Project$", "")
                        region = group["Keys"][1]
                        
                        # Extract cost
                        cost = 0
                        if "Metrics" in group and "AmortizedCost" in group["Metrics"]:
                            cost = float(group["Metrics"]["AmortizedCost"]["Amount"])
                        
                        # Add to appropriate dataset
                        record = {"Project": project, "Region": region, "Cost": cost}
                        all_regions_data.append(record)
                        
                        # Check if this is an untagged resource
                        if project.lower() in ['', 'none', 'null', 'untagged']:
                            untagged_regions_data.append(record)
        
        # Convert to dataframes
        if all_regions_data:
            all_regions_df = pd.DataFrame(all_regions_data)
            # Create a region summary
            region_summary_df = all_regions_df.groupby("Region")["Cost"].sum().reset_index().sort_values("Cost", ascending=False)
        else:
            all_regions_df = None
            region_summary_df = None
            
        # Create untagged resources dataframe
        if untagged_regions_data:
            untagged_regions_df = pd.DataFrame(untagged_regions_data)
            untagged_regions_df = untagged_regions_df.groupby("Region")["Cost"].sum().reset_index().sort_values("Cost", ascending=False)
        else:
            untagged_regions_df = None
        
        return region_summary_df, untagged_regions_df, all_regions_df
    
    except Exception as e:
        st.warning(f"Error processing AWS region data: {str(e)}")
        return None, None, None

def process_aws_project_resources(aws_data):
    """Process AWS project-by-resource data"""
    if not aws_data or "project_by_resource" not in aws_data or not aws_data["project_by_resource"]:
        return {}
    
    try:
        # Process the resource data grouped by project
        resource_data = aws_data["project_by_resource"]
        
        # Extract projects and their resources
        project_resources_data = []
        
        # Process the data based on AWS Cost Explorer response structure
        for time_result in resource_data.get("ResultsByTime", []):
            if "Groups" in time_result:
                for group in time_result["Groups"]:
                    # Keys are typically [Project, ResourceId]
                    if len(group["Keys"]) >= 2:
                        project = group["Keys"][0].replace("Project$", "")
                        resource_id = group["Keys"][1]
                        
                        # Extract the resource type and name from the resource ID
                        resource_parts = resource_id.split('/')
                        resource_type = resource_parts[0] if len(resource_parts) > 0 else "Unknown"
                        resource_name = resource_parts[-1] if len(resource_parts) > 1 else resource_id
                        
                        # Extract cost
                        cost = 0
                        if "Metrics" in group and "AmortizedCost" in group["Metrics"]:
                            cost = float(group["Metrics"]["AmortizedCost"]["Amount"])
                        
                        project_resources_data.append({
                            "Project": project,
                            "ResourceId": resource_id,
                            "ResourceType": resource_type,
                            "ResourceName": resource_name,
                            "Cost": cost
                        })
        
        # Convert to dataframe
        if project_resources_data:
            project_resources_df = pd.DataFrame(project_resources_data)
            # Group by project to create a dictionary of dataframes
            project_resources_dict = {}
            for project, group in project_resources_df.groupby("Project"):
                project_resources_dict[project] = group.sort_values("Cost", ascending=False)
            return project_resources_dict
        else:
            return {}
    
    except Exception as e:
        st.warning(f"Error processing AWS resource data: {str(e)}")
        return {}

# ----- Azure Cost Functions -----
def get_azure_costs_from_files():
    """Read Azure cost data from MinIO bucket"""
    try:
        # Initialize S3 filesystem connection to MinIO
        fs = s3fs.S3FileSystem(
            endpoint_url=os.environ.get("MINIO_ENDPOINT", MINIO_ENDPOINT),
            key=os.environ.get("MINIO_ACCESS_KEY", MINIO_ACCESS_KEY),
            secret=os.environ.get("MINIO_SECRET_KEY", MINIO_SECRET_KEY),
            use_ssl=True,
            client_kwargs={'verify': False},
            use_listings_cache=False,  # Disable cache
            skip_instance_cache=True   # Skip instance cache
        )
                
        # Path to Azure cost reports directory in MinIO bucket
        azure_dir = f"{MINIO_BUCKET}/azure-cost-reports"
        
        results = {}
        
        # Find all JSON files in the Azure directory
        azure_files = fs.glob(f"{azure_dir}/*.json")
        
        # Process each file based on its name
        for file_path in azure_files:
            file_name = os.path.basename(file_path)
            
            # Try to determine what dimension this file represents
            dimension = None
            if "ResourceGroup" in file_name:
                dimension = "ResourceGroupName"
            elif "Service" in file_name:
                dimension = "ServiceName"
            elif "project" in file_name.lower() and "region" in file_name.lower():
                dimension = "project_by_region"
            elif "project" in file_name.lower() and "resource" in file_name.lower():
                dimension = "project_by_resource"
            elif "project" in file_name.lower():
                dimension = "project"
            elif "meter" in file_name.lower() and "category" in file_name.lower():
                dimension = "MeterCategory"
            elif "meter" in file_name.lower() and "sub" in file_name.lower():
                dimension = "MeterSubCategory"
            elif "resource" in file_name.lower() and "type" in file_name.lower():
                dimension = "ResourceType"
            elif "billing_cycle_total" in file_name.lower():
                dimension = "billing_cycle"
            else:
                # If can't determine, use the filename without extension
                dimension = os.path.splitext(file_name)[0]
            
            # Load the JSON data
            with fs.open(file_path, 'r') as f:
                results[dimension] = json.load(f)
        
        return results
    except Exception as e:
        st.error(f"Error reading Azure cost data from MinIO: {str(e)}")
        return None

def process_azure_data(azure_data):
    """Process Azure cost data for display"""
    if not azure_data:
        return None, None, None
    
    # Process resource group data
    rg_df = None
    rg_data = azure_data.get("ResourceGroupName")
    if rg_data:
        try:
            cols = [c["name"] for c in rg_data["properties"]["columns"]]
            rows = rg_data["properties"]["rows"]
            rg_df = pd.DataFrame(rows, columns=cols)
            rg_df = rg_df.rename(columns={c: "Cost" for c in rg_df.columns if "cost" in c.lower()})
            
            # Get the currency column first (to preserve it for the aggregation)
            currency_col = None
            for col in rg_df.columns:
                if col not in ["ResourceGroupName", "Cost"] and "currency" in col.lower():
                    currency_col = col
                    rg_df = rg_df.rename(columns={col: "Currency"})
                    break
            
            # Select columns and convert Cost to numeric
            if currency_col:
                rg_df = rg_df[["ResourceGroupName", "Cost", "Currency"]]
            else:
                rg_df = rg_df[["ResourceGroupName", "Cost"]]
                
            rg_df["Cost"] = pd.to_numeric(rg_df["Cost"])
            
            # Group by ResourceGroupName and sum the costs
            if currency_col:
                # Need to keep the Currency column when grouping
                rg_df = rg_df.groupby(["ResourceGroupName", "Currency"], as_index=False).sum()
            else:
                rg_df = rg_df.groupby("ResourceGroupName", as_index=False).sum()
                
            # Sort by cost descending
            rg_df = rg_df.sort_values("Cost", ascending=False)
        except Exception as e:
            st.warning(f"Could not process Azure resource group data: {str(e)}")
    
    # Process service data
    service_df = None
    service_data = azure_data.get("ServiceName")
    if service_data:
        try:
            cols = [c["name"] for c in service_data["properties"]["columns"]]
            rows = service_data["properties"]["rows"]
            service_df = pd.DataFrame(rows, columns=cols)
            service_df = service_df.rename(columns={c: "Cost" for c in service_df.columns if "cost" in c.lower()})
            
            # Get the currency column first (to preserve it for the aggregation)
            currency_col = None
            for col in service_df.columns:
                if col not in ["ServiceName", "Cost"] and "currency" in col.lower():
                    currency_col = col
                    service_df = service_df.rename(columns={col: "Currency"})
                    break
            
            # Select columns and convert Cost to numeric
            if currency_col:
                service_df = service_df[["ServiceName", "Cost", "Currency"]]
            else:
                service_df = service_df[["ServiceName", "Cost"]]
                
            service_df["Cost"] = pd.to_numeric(service_df["Cost"])
            
            # Group by ServiceName and sum the costs
            if currency_col:
                # Need to keep the Currency column when grouping
                service_df = service_df.groupby(["ServiceName", "Currency"], as_index=False).sum()
            else:
                service_df = service_df.groupby("ServiceName", as_index=False).sum()
                
            # Sort by cost descending
            service_df = service_df.sort_values("Cost", ascending=False)
        except Exception as e:
            st.warning(f"Could not process Azure service data: {str(e)}")
    
    # Process project tag data
    project_df = None
    project_data = azure_data.get("project")
    if project_data:
        try:
            cols = [c["name"] for c in project_data["properties"]["columns"]]
            rows = project_data["properties"]["rows"]
            project_df = pd.DataFrame(rows, columns=cols)
            
            # Handle different formats and rename columns
            project_col = None
            if "TagValue" in project_df.columns:
                project_df = project_df.rename(columns={"TagValue": "Project"})
                project_col = "Project"
            else:
                # Find the project column
                for col in project_df.columns:
                    if col != "Cost" and "currency" not in col.lower():
                        project_col = col
                        project_df = project_df.rename(columns={col: "Project"})
                        break
            
            # Rename cost column
            for col in project_df.columns:
                if "cost" in col.lower():
                    project_df = project_df.rename(columns={col: "Cost"})
                    break
            
            # Get the currency column
            currency_col = None
            for col in project_df.columns:
                if "currency" in col.lower():
                    currency_col = col
                    project_df = project_df.rename(columns={col: "Currency"})
                    break
            
            # Fill NA in Project column
            project_df["Project"] = project_df["Project"].fillna("Untagged")
            project_df["Cost"] = pd.to_numeric(project_df["Cost"])
            
            # Select columns
            if currency_col:
                project_df = project_df[["Project", "Cost", "Currency"]]
                # Group by Project and Currency
                project_df = project_df.groupby(["Project", "Currency"], as_index=False).sum()
            else:
                project_df = project_df[["Project", "Cost"]]
                # Group by Project
                project_df = project_df.groupby("Project", as_index=False).sum()
            
            # Sort by cost descending
            project_df = project_df.sort_values("Cost", ascending=False)
        except Exception as e:
            st.warning(f"Could not process Azure project data: {str(e)}")
    
    return rg_df, service_df, project_df

def process_azure_region_data(azure_data):
    """Process Azure regional cost data"""
    if not azure_data or "project_by_region" not in azure_data:
        return None, None, None
    
    try:
        # Process the region data grouped by project tag and location
        region_data = azure_data["project_by_region"]
        
        # Extract column names and rows
        if "properties" in region_data and "columns" in region_data["properties"] and "rows" in region_data["properties"]:
            cols = [c["name"] for c in region_data["properties"]["columns"]]
            rows = region_data["properties"]["rows"]
            
            # Create a DataFrame
            region_df = pd.DataFrame(rows, columns=cols)
            
            # Rename columns for consistency
            for col in region_df.columns:
                if "TagValue" in col:
                    region_df = region_df.rename(columns={col: "Project"})
                elif "ResourceLocation" in col:
                    region_df = region_df.rename(columns={col: "Region"})
                elif "cost" in col.lower():
                    region_df = region_df.rename(columns={col: "Cost"})
                elif "currency" in col.lower():
                    region_df = region_df.rename(columns={col: "Currency"})
            
            # Fill NA values and convert cost to numeric
            region_df["Project"] = region_df["Project"].fillna("Untagged")
            region_df["Cost"] = pd.to_numeric(region_df["Cost"])
            
            # Create region summary
            region_summary_df = region_df.groupby("Region")["Cost"].sum().reset_index().sort_values("Cost", ascending=False)
            
            # Create untagged resources dataframe
            untagged_mask = (region_df["Project"].str.lower().isin(['', 'none', 'null', 'untagged']))
            untagged_regions_df = region_df[untagged_mask].groupby("Region")["Cost"].sum().reset_index().sort_values("Cost", ascending=False)
            
            return region_summary_df, untagged_regions_df, region_df
        else:
            return None, None, None
    
    except Exception as e:
        st.warning(f"Error processing Azure region data: {str(e)}")
        return None, None, None

def process_azure_project_resources(azure_data):
    """Process Azure project-by-resource data"""
    if not azure_data or "project_by_resource" not in azure_data:
        return {}
    
    try:
        # Process the resource data grouped by project tag and resource id
        resource_data = azure_data["project_by_resource"]
        
        # Extract column names and rows
        if "properties" in resource_data and "columns" in resource_data["properties"] and "rows" in resource_data["properties"]:
            cols = [c["name"] for c in resource_data["properties"]["columns"]]
            rows = resource_data["properties"]["rows"]
            
            # Create a DataFrame
            resource_df = pd.DataFrame(rows, columns=cols)
            
            # Rename columns for consistency
            for col in resource_df.columns:
                if "TagValue" in col:
                    resource_df = resource_df.rename(columns={col: "Project"})
                elif "ResourceId" in col:
                    resource_df = resource_df.rename(columns={col: "ResourceId"})
                elif "cost" in col.lower():
                    resource_df = resource_df.rename(columns={col: "Cost"})
                elif "currency" in col.lower():
                    resource_df = resource_df.rename(columns={col: "Currency"})
            
            # Fill NA values and convert cost to numeric
            resource_df["Project"] = resource_df["Project"].fillna("Untagged")
            resource_df["Cost"] = pd.to_numeric(resource_df["Cost"])
            
            # Extract resource type and name from ResourceId
            def extract_resource_info(resource_id):
                parts = resource_id.split('/')
                # Azure ResourceIds typically follow a pattern with the resource type and name at the end
                resource_type = parts[-2] if len(parts) >= 2 else "Unknown"
                resource_name = parts[-1] if len(parts) >= 1 else resource_id
                return resource_type, resource_name
            
            # Apply the extraction function
            resource_info = resource_df["ResourceId"].apply(extract_resource_info)
            resource_df["ResourceType"] = resource_info.apply(lambda x: x[0])
            resource_df["ResourceName"] = resource_info.apply(lambda x: x[1])
            
            # Group by project to create a dictionary of dataframes
            project_resources_dict = {}
            for project, group in resource_df.groupby("Project"):
                project_resources_dict[project] = group.sort_values("Cost", ascending=False)
            
            return project_resources_dict
        else:
            return {}
    
    except Exception as e:
        st.warning(f"Error processing Azure resource data: {str(e)}")
        return {}

# ----- PDF Export Function -----
def create_download_link(val, filename):
    b64 = base64.b64encode(val)
    return f'<a href="data:application/octet-stream;base64,{b64.decode()}" download="{filename}" class="download-button">Download PDF Report</a>'

def export_as_pdf(aws_daily_df, aws_service_df, aws_project_df, azure_rg_df, azure_service_df, azure_project_df, 
                 aws_total, azure_total_inr, azure_total_usd, combined_total, inr_to_usd_rate,
                 aws_billing_cycle, azure_billing_cycle, 
                 aws_region_summary_df, azure_region_summary_df,
                 aws_untagged_regions_df, azure_untagged_regions_df,
                 aws_project_resources_dict, azure_project_resources_dict):
    """Create a PDF report with properly sized tables and charts"""
    # In the PDF creation process, we need to ensure we don't include index columns
    # This requires updating how we iterate through dataframes when creating tables
    
    # Example of how a table is created in the PDF:
    # Add headers
    # pdf.cell(180, 10, 'Region', 1, 0, 'L')
    # pdf.cell(60, 10, 'Cost ($)', 1, 1, 'R')
    # 
    # # Add data rows - this is where we skip the index column
    # for _, row in df.iterrows():
    #     pdf.cell(180, 8, str(row['Region']), 1, 0, 'L')
    #     pdf.cell(60, 8, f"${row['Cost']:.2f}", 1, 1, 'R')
    
    # No need to change the above pattern as the index is already ignored when 
    # we use "for _, row in df.iterrows()" - the underscore discards the index
    
    pdf = FPDF()
    # Use landscape orientation for better chart display
    pdf.add_page('L')
    
    # Set up the PDF
    pdf.set_font('Arial', 'B', 16)
    pdf.cell(0, 10, 'Cloud Cost Report', 0, 1, 'C')
    pdf.cell(0, 10, f'{start_date} to {end_date}', 0, 1, 'C')
    pdf.ln(10)  # Increased spacing
    
    # Save the starting Y position to align summaries with pie chart
    start_y = pdf.get_y()
    
    # Generate the pie chart first to have it ready
    pie_chart_image = None
    if aws_total > 0 or azure_total_usd > 0:
        plt.figure(figsize=(8, 6))
        plt.pie([aws_total, azure_total_usd], 
               labels=["AWS", "Azure"], 
               autopct='%1.1f%%',
               colors=['#FF9900', '#0089D6'])
        plt.title("Cost Distribution by Cloud Provider (USD)")
        
        # Create a temporary file for the image
        pie_chart_image = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        plt.savefig(pie_chart_image.name, format='png', dpi=150)
        plt.close()
    
    # Left side: Cost Summaries
    # Weekly Cost Summary
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(140, 10, 'Weekly Cost Summary', 0, 1, 'L')
    pdf.ln(5)  # Add space before cost details
    pdf.set_font('Arial', '', 12)
    
    # Use half width cell for each cost value to stay on the left side
    pdf.cell(140, 10, f'AWS Total: ${aws_total:.2f}', 0, 1, 'L')
    # Use INR text instead of symbol to avoid encoding issues
    pdf.cell(140, 10, f'Azure Total: INR {azure_total_inr:.2f} (${azure_total_usd:.2f})', 0, 1, 'L')
    pdf.cell(140, 10, f'Combined Total: ${combined_total:.2f}', 0, 1, 'L')
    pdf.ln(10)  # Increased spacing
    
    # Billing Cycle Summary
    aws_cycle_total = aws_billing_cycle.get("total_cost", 0) if aws_billing_cycle else 0
    azure_cycle_total_inr = azure_billing_cycle.get("total_cost", 0) if azure_billing_cycle else 0
    azure_cycle_total_usd = azure_cycle_total_inr * inr_to_usd_rate
    combined_cycle_total = aws_cycle_total + azure_cycle_total_usd
    
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(140, 10, 'Billing Cycle Summary', 0, 1, 'L')
    pdf.ln(5)  # Add space before billing cycle details
    pdf.set_font('Arial', '', 12)
    
    # Use half width cell for each billing cycle value to stay on the left side
    pdf.cell(140, 10, f'AWS Billing Cycle: ${aws_cycle_total:.2f}', 0, 1, 'L')
    pdf.cell(140, 10, f'Azure Billing Cycle: INR {azure_cycle_total_inr:.2f} (${azure_cycle_total_usd:.2f})', 0, 1, 'L')
    pdf.cell(140, 10, f'Combined Billing Cycle: ${combined_cycle_total:.2f}', 0, 1, 'L')
    pdf.ln(5)  # Reduced spacing
    
    # Currency exchange rate information
    pdf.set_font('Arial', 'I', 10)
    pdf.cell(140, 10, f'Exchange Rate: $1 USD = INR {1/inr_to_usd_rate:.2f}', 0, 1, 'L')
    
    # Right side: Place the pie chart
    if pie_chart_image:
        # Position the chart on the right side starting from the same Y as the summaries
        pdf.image(pie_chart_image.name, x=150, y=start_y, w=120)
        # Remove the temporary file
        os.unlink(pie_chart_image.name)
    
    # Regional Cost Analysis on a new page
    pdf.add_page('L')
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(0, 10, 'Regional Cost Analysis', 0, 1, 'L')
    pdf.ln(5) # Add extra spacing
    
    # AWS Regional Analysis
    if aws_region_summary_df is not None and not aws_region_summary_df.empty:
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, 'AWS Costs by Region', 0, 1, 'L')
        pdf.ln(5) # Add extra spacing
        
        plt.figure(figsize=(12, 8))
        x = range(len(aws_region_summary_df))
        plt.bar(x, aws_region_summary_df["Cost"])
        plt.xticks(x, aws_region_summary_df["Region"], rotation=45, ha="right")
        plt.title("AWS Costs by Region", fontsize=18)
        plt.ylabel("USD ($)")
        plt.tight_layout(pad=2.0)
        
        # Create a temporary file for the image
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
            plt.savefig(tmpfile.name, format='png', dpi=150)
            plt.close()
            # Add the image to the PDF
            pdf.image(tmpfile.name, x=20, y=pdf.get_y(), w=250)
        # Remove the temporary file
        os.unlink(tmpfile.name)
        
        # Add region summary table - increased spacing after chart
        pdf.ln(150)  # More space after the chart to avoid overlap
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, 'AWS Regional Costs Table', 0, 1, 'L')
        pdf.ln(5) # Add extra spacing
        
        # Region costs table
        pdf.set_font('Arial', '', 8)
        
        # Add headers
        pdf.cell(180, 10, 'Region', 1, 0, 'L')
        pdf.cell(60, 10, 'Cost ($)', 1, 1, 'R')
        
        # Add data rows
        for _, row in aws_region_summary_df.iterrows():
            pdf.cell(180, 8, str(row['Region']), 1, 0, 'L')
            pdf.cell(60, 8, f"${row['Cost']:.2f}", 1, 1, 'R')
        
        # Add total row
        pdf.set_font('Arial', 'B', 8)
        pdf.cell(180, 8, 'TOTAL', 1, 0, 'L')
        pdf.cell(60, 8, f"${aws_region_summary_df['Cost'].sum():.2f}", 1, 1, 'R')
    
    # Azure Regional Analysis
    if azure_region_summary_df is not None and not azure_region_summary_df.empty:
        # Use a new page for Azure regions to ensure no overlap
        pdf.add_page('L')
        pdf.set_font('Arial', 'B', 14)
        pdf.cell(0, 10, 'Azure Costs by Region', 0, 1, 'L')
        pdf.ln(5) # Add extra spacing
        
        # Make chart slightly smaller and adjust its proportions
        plt.figure(figsize=(10, 6))
        x = range(len(azure_region_summary_df))
        plt.bar(x, azure_region_summary_df["Cost"])
        plt.xticks(x, azure_region_summary_df["Region"], rotation=45, ha="right")
        plt.title("Azure Costs by Region", fontsize=16)
        plt.ylabel("INR")
        plt.tight_layout(pad=3.0)  # More padding
        
        # Create a temporary file for the image
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
            plt.savefig(tmpfile.name, format='png', dpi=150)
            plt.close()
            # Add the image to the PDF with slightly reduced width
            pdf.image(tmpfile.name, x=20, y=pdf.get_y(), w=220)
        # Remove the temporary file
        os.unlink(tmpfile.name)
        
        # Much more space after the chart to ensure no overlap
        pdf.ln(170)  # Increased spacing
        
        # Add region summary table
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, 'Azure Regional Costs Table', 0, 1, 'L')
        pdf.ln(5) # Add extra spacing before the table
        
        # Region costs table
        pdf.set_font('Arial', '', 8)
        
        # Add headers with slightly adjusted widths
        pdf.cell(170, 10, 'Region', 1, 0, 'L')
        pdf.cell(70, 10, 'Cost (INR)', 1, 1, 'R')
        
        # Add data rows with consistent widths
        for _, row in azure_region_summary_df.iterrows():
            pdf.cell(170, 8, str(row['Region']), 1, 0, 'L')
            pdf.cell(70, 8, f"INR {row['Cost']:.2f}", 1, 1, 'R')
        
        # Add total row with consistent widths
        pdf.set_font('Arial', 'B', 8)
        pdf.cell(170, 8, 'TOTAL', 1, 0, 'L')
        pdf.cell(70, 8, f"INR {azure_region_summary_df['Cost'].sum():.2f}", 1, 1, 'R')
    
    # AWS Untagged Resources by Region
    if aws_untagged_regions_df is not None and not aws_untagged_regions_df.empty:
        pdf.add_page('L')
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, 'AWS Untagged Resources by Region', 0, 1, 'L')
        pdf.ln(5) # Add extra spacing
        
        # Create bar chart instead of pie
        if not aws_untagged_regions_df.empty:
            plt.figure(figsize=(12, 8))
            x = range(len(aws_untagged_regions_df))
            plt.bar(x, aws_untagged_regions_df["Cost"])
            plt.xticks(x, aws_untagged_regions_df["Region"], rotation=45, ha="right")
            plt.title("AWS Untagged Resources by Region", fontsize=18)
            plt.ylabel("USD ($)")
            plt.tight_layout(pad=2.0)
            
            # Create a temporary file for the image
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
                plt.savefig(tmpfile.name, format='png', dpi=150)
                plt.close()
                # Add the image to the PDF
                pdf.image(tmpfile.name, x=20, y=pdf.get_y(), w=250)
            # Remove the temporary file
            os.unlink(tmpfile.name)
            
            pdf.ln(150)  # More space after chart
        
        # After the chart and before the table title
        pdf.add_page('L')  # Start a new landscape page
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, 'AWS Untagged Resources by Region Table', 0, 1, 'L')
        pdf.ln(5) # Reduced spacing since we're on a new page
        
        # Table
        pdf.set_font('Arial', '', 8)
        
        # Add headers
        pdf.cell(180, 10, 'Region', 1, 0, 'L')
        pdf.cell(60, 10, 'Cost ($)', 1, 1, 'R')
        
        # Add data rows
        for _, row in aws_untagged_regions_df.iterrows():
            pdf.cell(180, 8, str(row['Region']), 1, 0, 'L')
            pdf.cell(60, 8, f"${row['Cost']:.2f}", 1, 1, 'R')
        
        # Add total row
        pdf.set_font('Arial', 'B', 8)
        pdf.cell(180, 8, 'TOTAL', 1, 0, 'L')
        pdf.cell(60, 8, f"${aws_untagged_regions_df['Cost'].sum():.2f}", 1, 1, 'R')
    
    # Azure Untagged Resources by Region
    if azure_untagged_regions_df is not None and not azure_untagged_regions_df.empty:
        pdf.add_page('L')
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, 'Azure Untagged Resources by Region', 0, 1, 'L')
        pdf.ln(5) # Add extra spacing
        
        # Create bar chart instead of pie
        if not azure_untagged_regions_df.empty:
            plt.figure(figsize=(12, 8))
            x = range(len(azure_untagged_regions_df))
            plt.bar(x, azure_untagged_regions_df["Cost"])
            plt.xticks(x, azure_untagged_regions_df["Region"], rotation=45, ha="right")
            plt.title("Azure Untagged Resources by Region", fontsize=18)
            plt.ylabel("INR")
            plt.tight_layout(pad=2.0)
            
            # Create a temporary file for the image
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
                plt.savefig(tmpfile.name, format='png', dpi=150)
                plt.close()
                # Add the image to the PDF
                pdf.image(tmpfile.name, x=20, y=pdf.get_y(), w=250)
            # Remove the temporary file
            os.unlink(tmpfile.name)
            
            pdf.ln(150)  # More space after chart
        
        # Add untagged resources table
        pdf.add_page('L')  # Start a new landscape page
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, 'Azure Untagged Resources by Region Table', 0, 1, 'L')
        pdf.ln(5) # Add extra spacing
        
        # Table
        pdf.set_font('Arial', '', 8)
        
        # Add headers
        pdf.cell(180, 10, 'Region', 1, 0, 'L')
        pdf.cell(60, 10, 'Cost (INR)', 1, 1, 'R')
        
        # Add data rows
        for _, row in azure_untagged_regions_df.iterrows():
            pdf.cell(180, 8, str(row['Region']), 1, 0, 'L')
            pdf.cell(60, 8, f"INR {row['Cost']:.2f}", 1, 1, 'R')
        
        # Add total row
        pdf.set_font('Arial', 'B', 8)
        pdf.cell(180, 8, 'TOTAL', 1, 0, 'L')
        pdf.cell(60, 8, f"INR {azure_untagged_regions_df['Cost'].sum():.2f}", 1, 1, 'R')
    
    # AWS Section (continue with the existing sections...)
    ## ... existing code ...
    
    # AWS Project Resource Breakdown (add top projects)
    if aws_project_resources_dict:
        # Take top 3 projects by cost
        top_projects = []
        for project, df in aws_project_resources_dict.items():
            if not df.empty:
                total_cost = df["Cost"].sum()
                top_projects.append((project, total_cost, df))
        
        # Sort by cost and take top 3
        top_projects.sort(key=lambda x: x[1], reverse=True)
        top_projects = top_projects[:3]
        
        for project, total, df in top_projects:
            pdf.add_page('L')
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 10, f'AWS Project Resources: {project}', 0, 1, 'L')
            
            # Add resource type breakdown chart
            resource_type_summary = df.groupby("ResourceType")["Cost"].sum().reset_index().sort_values("Cost", ascending=False).head(20)
            
            # Always create the chart regardless of the number of resource types
            plt.figure(figsize=(10, 6))
            x = range(len(resource_type_summary))
            plt.bar(x, resource_type_summary["Cost"])
            plt.xticks(x, resource_type_summary["ResourceType"], rotation=45, ha="right")
            plt.title(f"{project}: Cost by Resource Type", fontsize=16)
            plt.ylabel("USD ($)")
            plt.tight_layout(pad=2.0)
            
            # Create a temporary file for the image
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
                plt.savefig(tmpfile.name, format='png', dpi=150)
                plt.close()
                # Add the image to the PDF
                pdf.image(tmpfile.name, x=20, y=pdf.get_y(), w=250)
            # Remove the temporary file
            os.unlink(tmpfile.name)
            
            pdf.ln(150)  # Space after chart
            
            # Add top resources table
            pdf.add_page('L')  # Start a new landscape page
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 10, f'Top Resources for {project}', 0, 1, 'L')
            pdf.ln(5) # Add extra spacing
            
            # Table
            pdf.set_font('Arial', '', 8)
            
            # Add headers
            pdf.cell(100, 10, 'Resource Type', 1, 0, 'L')
            pdf.cell(140, 10, 'Resource Name', 1, 0, 'L')
            pdf.cell(40, 10, 'Cost ($)', 1, 1, 'R')
            
            # Add data rows (top 15 resources)
            for _, row in df.head(15).iterrows():
                resource_type = str(row['ResourceType'])
                resource_name = str(row['ResourceName'])
                # Truncate long names
                if len(resource_type) > 45:
                    resource_type = resource_type[:42] + "..."
                if len(resource_name) > 65:
                    resource_name = resource_name[:62] + "..."
                
                pdf.cell(100, 8, resource_type, 1, 0, 'L')
                pdf.cell(140, 8, resource_name, 1, 0, 'L')
                pdf.cell(40, 8, f"${row['Cost']:.2f}", 1, 1, 'R')
            
            # Add total row
            pdf.set_font('Arial', 'B', 8)
            pdf.cell(240, 8, 'TOTAL', 1, 0, 'L')
            pdf.cell(40, 8, f"${df['Cost'].sum():.2f}", 1, 1, 'R')
    
    # Azure Project Resource Breakdown (add top projects)
    if azure_project_resources_dict:
        # Take top 3 projects by cost
        top_projects = []
        for project, df in azure_project_resources_dict.items():
            if not df.empty:
                total_cost = df["Cost"].sum()
                top_projects.append((project, total_cost, df))
        
        # Sort by cost and take top 3
        top_projects.sort(key=lambda x: x[1], reverse=True)
        top_projects = top_projects[:3]
        
        for project, total, df in top_projects:
            pdf.add_page('L')
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 10, f'Azure Project Resources: {project}', 0, 1, 'L')
            
            # Add resource type breakdown chart
            resource_type_summary = df.groupby("ResourceType")["Cost"].sum().reset_index().sort_values("Cost", ascending=False)
            
            # Always create the chart regardless of the number of resource types
            plt.figure(figsize=(10, 6))
            x = range(len(resource_type_summary))
            plt.bar(x, resource_type_summary["Cost"])
            plt.xticks(x, resource_type_summary["ResourceType"], rotation=45, ha="right")
            plt.title(f"{project}: Cost by Resource Type", fontsize=16)
            plt.ylabel("INR")
            plt.tight_layout(pad=2.0)
            
            # Create a temporary file for the image
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
                plt.savefig(tmpfile.name, format='png', dpi=150)
                plt.close()
                # Add the image to the PDF
                pdf.image(tmpfile.name, x=20, y=pdf.get_y(), w=250)
            # Remove the temporary file
            os.unlink(tmpfile.name)
            
            pdf.ln(150)  # Space after chart
            
            # Add top resources table
            pdf.add_page('L')  # Start a new landscape page
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 10, f'Top Resources for {project}', 0, 1, 'L')
            pdf.ln(5) # Add extra spacing
            
            # Table
            pdf.set_font('Arial', '', 8)
            
            # Add headers
            pdf.cell(100, 10, 'Resource Type', 1, 0, 'L')
            pdf.cell(140, 10, 'Resource Name', 1, 0, 'L')
            pdf.cell(40, 10, 'Cost (INR)', 1, 1, 'R')
            
            # Add data rows (top 15 resources)
            for _, row in df.head(15).iterrows():
                resource_type = str(row['ResourceType'])
                resource_name = str(row['ResourceName'])
                # Truncate long names
                if len(resource_type) > 45:
                    resource_type = resource_type[:42] + "..."
                if len(resource_name) > 65:
                    resource_name = resource_name[:62] + "..."
                
                pdf.cell(100, 8, resource_type, 1, 0, 'L')
                pdf.cell(140, 8, resource_name, 1, 0, 'L')
                pdf.cell(40, 8, f"INR {row['Cost']:.2f}", 1, 1, 'R')
            
            # Add total row
            pdf.set_font('Arial', 'B', 8)
            pdf.cell(240, 8, 'TOTAL', 1, 0, 'L')
            pdf.cell(40, 8, f"INR {df['Cost'].sum():.2f}", 1, 1, 'R')
    
    # Add Combined Project Costs Across Clouds section
    if aws_project_df is not None and azure_project_df is not None:
        pdf.add_page('L')
        pdf.set_font('Arial', 'B', 14)
        pdf.cell(0, 10, 'Combined Cloud Costs (Converted to USD)', 0, 1, 'L')
        pdf.ln(5)
        
        # Add caption about currency conversion
        pdf.set_font('Arial', 'I', 10)
        pdf.cell(0, 10, f'Azure costs have been converted from INR to USD for comparison | Exchange Rate: $1 USD = INR {1/inr_to_usd_rate:.2f}', 0, 1, 'L')
        pdf.ln(10)
        
        # Prepare data
        aws_project_df_copy = aws_project_df.copy()
        aws_project_df_copy["Cloud"] = "AWS"
        aws_project_df_copy = aws_project_df_copy[["Project", "Cost", "Cloud"]]
        
        azure_project_df_copy = azure_project_df.copy()
        # Convert Azure costs from INR to USD for fair comparison
        azure_project_df_copy["Cost"] = azure_project_df_copy["Cost"] * inr_to_usd_rate
        azure_project_df_copy["Cloud"] = "Azure"
        azure_project_df_copy = azure_project_df_copy[["Project", "Cost", "Cloud"]]
        
        # Combine
        combined_projects = pd.concat([aws_project_df_copy, azure_project_df_copy])
        
        # Pivot for comparison
        pivot_df = combined_projects.pivot_table(
            index="Project", 
            columns="Cloud", 
            values="Cost", 
            aggfunc="sum",
            fill_value=0
        ).reset_index()
        
        # Calculate totals
        pivot_df["Total"] = pivot_df["AWS"] + pivot_df["Azure"]
        pivot_df = pivot_df.sort_values("Total", ascending=False)
        
        # Table title
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, 'Project Costs Across Clouds Table (USD)', 0, 1, 'L')
        pdf.ln(5)
        
        # Create a bar chart first
        top_projects = pivot_df.head(10)
        plt.figure(figsize=(12, 6))
        width = 0.35
        x = range(len(top_projects))
        plt.bar([i - width/2 for i in x], top_projects["AWS"], width, label="AWS", color="#FF9900")
        plt.bar([i + width/2 for i in x], top_projects["Azure"], width, label="Azure (Converted to USD)", color="#0089D6")
        plt.ylabel("Cost ($)")
        plt.xticks(x, top_projects["Project"], rotation=45, ha="right")
        plt.legend()
        plt.title("Top 10 Projects by Cost (USD)", fontsize=16)
        plt.tight_layout(pad=2.0)
        
        # Create a temporary file for the image
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
            plt.savefig(tmpfile.name, format='png', dpi=150)
            plt.close()
            # Add the image to the PDF
            pdf.image(tmpfile.name, x=20, y=pdf.get_y(), w=250)
        # Remove the temporary file
        os.unlink(tmpfile.name)
        
        pdf.ln(150)  # Space after chart
        
        # Add combined projects table
        pdf.set_font('Arial', '', 8)
        
        # Add headers
        pdf.cell(120, 10, 'Project', 1, 0, 'L')
        pdf.cell(50, 10, 'AWS ($)', 1, 0, 'R')
        pdf.cell(50, 10, 'Azure ($)', 1, 0, 'R')
        pdf.cell(60, 10, 'Total ($)', 1, 1, 'R')
        
        # Add data rows
        for _, row in pivot_df.iterrows():
            pdf.cell(120, 8, str(row['Project']), 1, 0, 'L')
            pdf.cell(50, 8, f"${row['AWS']:.2f}", 1, 0, 'R')
            pdf.cell(50, 8, f"${row['Azure']:.2f}", 1, 0, 'R')
            pdf.cell(60, 8, f"${row['Total']:.2f}", 1, 1, 'R')
        
        # Add total row
        pdf.set_font('Arial', 'B', 8)
        pdf.cell(120, 8, 'TOTAL', 1, 0, 'L')
        pdf.cell(50, 8, f"${aws_total:.2f}", 1, 0, 'R')
        pdf.cell(50, 8, f"${azure_total_usd:.2f}", 1, 0, 'R')
        pdf.cell(60, 8, f"${combined_total:.2f}", 1, 1, 'R')
    
    # Footer
    pdf.set_y(-10)
    pdf.set_font('Arial', 'I', 8)
    pdf.cell(0, 10, f'Report generated on {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', 0, 0, 'C')
    
    # Modify the final output encoding to handle unicode safely
    try:
        return pdf.output(dest='S').encode('latin1')
    except UnicodeEncodeError:
        # If we encounter a Unicode error, try to produce a PDF with just ASCII characters
        print("Warning: Encountered Unicode encoding issue - generating simplified PDF")
        return pdf.output(dest='S').encode('ascii', 'replace')

# ----- Main app logic -----
with st.spinner("Loading cloud cost data..."):
    # Get AWS costs from files
    aws_data = None
    aws_daily_df = aws_service_df = aws_project_df = None
    aws_billing_cycle = None
    aws_region_summary_df = aws_untagged_regions_df = aws_all_regions_df = None
    aws_project_resources_dict = {}
    
    aws_data = get_aws_costs_from_files()
    if aws_data:
        aws_daily_df, aws_service_df, aws_project_df = process_aws_data(aws_data, "AmortizedCost")
        if "billing_cycle" in aws_data and aws_data["billing_cycle"]:
            aws_billing_cycle = aws_data["billing_cycle"]
        
        # Process AWS regional data
        aws_region_summary_df, aws_untagged_regions_df, aws_all_regions_df = process_aws_region_data(aws_data)
        
        # Process AWS project resources
        aws_project_resources_dict = process_aws_project_resources(aws_data)
    
    # Get Azure costs from files
    azure_data = None
    azure_rg_df = azure_service_df = azure_project_df = None
    azure_billing_cycle = None
    azure_region_summary_df = azure_untagged_regions_df = azure_all_regions_df = None
    azure_project_resources_dict = {}
    
    azure_data = get_azure_costs_from_files()
    if azure_data:
        azure_rg_df, azure_service_df, azure_project_df = process_azure_data(azure_data)
        if "billing_cycle" in azure_data and azure_data["billing_cycle"]:
            azure_billing_cycle = azure_data["billing_cycle"]
        
        # Process Azure regional data
        azure_region_summary_df, azure_untagged_regions_df, azure_all_regions_df = process_azure_region_data(azure_data)
        
        # Process Azure project resources
        azure_project_resources_dict = process_azure_project_resources(azure_data)

# Calculate totals
aws_total = aws_daily_df["Cost"].sum() if aws_daily_df is not None else 0
azure_total_inr = azure_rg_df["Cost"].sum() if azure_rg_df is not None else 0


cc = CurrencyConverter(currency_file=ECB_URL)
inr_to_usd_rate = cc.convert(1, 'INR', 'USD')

# Convert Azure cost from INR to USD for comparison
azure_total_usd = azure_total_inr * inr_to_usd_rate
combined_total = aws_total + azure_total_usd

# ----- Cost Summary Section -----
st.header("Cost Summary")

# Display cost summary metrics for weekly costs
st.subheader("Weekly Costs")
col1, col2, col3 = st.columns(3)
col1.metric("AWS Weekly Total", f"${aws_total:.2f}")
col2.metric("Azure Weekly Total", f"INR {azure_total_inr:.2f} (${azure_total_usd:.2f})")
col3.metric("Combined Weekly Total", f"${combined_total:.2f}")

# Display billing cycle costs if available
st.subheader("Current Billing Cycle")
col1, col2, col3 = st.columns(3)

# AWS Billing Cycle
aws_cycle_total = 0
if aws_billing_cycle:
    aws_cycle_total = aws_billing_cycle.get("total_cost", 0)
    aws_cycle_currency = aws_billing_cycle.get("currency", "USD")
    col1.metric("AWS Billing Cycle Total", f"${aws_cycle_total:.2f}")
else:
    col1.metric("AWS Billing Cycle Total", "Not available")

# Azure Billing Cycle
azure_cycle_total_inr = 0
azure_cycle_total_usd = 0
if azure_billing_cycle:
    azure_cycle_total_inr = azure_billing_cycle.get("total_cost", 0)
    azure_cycle_currency = azure_billing_cycle.get("currency", "INR")
    # Convert to USD if needed
    azure_cycle_total_usd = azure_cycle_total_inr * inr_to_usd_rate
    col2.metric("Azure Billing Cycle Total", f"INR {azure_cycle_total_inr:.2f} (${azure_cycle_total_usd:.2f})")
else:
    col2.metric("Azure Billing Cycle Total", "Not available")

# Combined Billing Cycle
combined_cycle_total = aws_cycle_total + azure_cycle_total_usd
col3.metric("Combined Billing Cycle Total", f"${combined_cycle_total:.2f}")

# Create a container for PDF export functionality
pdf_container = st.container()

# Main content continues with data visualization
# Pie chart of AWS vs Azure (using USD values for fair comparison)
st.subheader("Cost Distribution by Cloud Provider")

# Create a smaller column to contain the chart
col1, col2, col3 = st.columns([1, 2, 1])
with col2:  # Use the middle column
    # Create a reasonably sized figure with normal fonts for sharp rendering
    fig, ax = plt.subplots(figsize=(10, 10))
    
    # Use standard font sizes for the plot to keep it sharp
    ax.pie([aws_total, azure_total_usd], 
           labels=["AWS", "Azure"], 
           autopct='%1.1f%%',
           colors=['#FF9900', '#0089D6'])
    
    ax.set_title("Cost Distribution by Cloud Provider (USD)")
    
    # Display the plot, letting Streamlit handle the sizing
    st.pyplot(fig, use_container_width=True)

# Add PDF export button in the container created above
with pdf_container:
    if st.button("Generate PDF Report"):
        pdf_data = export_as_pdf(aws_daily_df, aws_service_df, aws_project_df, 
                            azure_rg_df, azure_service_df, azure_project_df,
                            aws_total, azure_total_inr, azure_total_usd, combined_total,
                            inr_to_usd_rate,
                            aws_billing_cycle, azure_billing_cycle,
                            aws_region_summary_df, azure_region_summary_df,
                            aws_untagged_regions_df, azure_untagged_regions_df,
                            aws_project_resources_dict, azure_project_resources_dict)
        
        html = create_download_link(pdf_data, "cloud_cost_report.pdf")
        st.markdown(html, unsafe_allow_html=True)
        st.success("PDF report generated! Click the button above to download.")

# Reset font size for subsequent plots
plt.rcParams.update({'font.size': plt.rcParamsDefault['font.size']})

# ----- Regional Cost Analysis Section -----
st.header("Regional Cost Analysis")

# AWS Region Analysis
st.subheader("AWS Regional Costs")
if aws_region_summary_df is not None and not aws_region_summary_df.empty:
    # Create two columns for side-by-side layout
    col1, col2 = st.columns(2)
    
    with col1:  # Left column for chart
        # Create region chart
        plt.figure(figsize=(8, 6))
        x = range(len(aws_region_summary_df))
        plt.bar(x, aws_region_summary_df["Cost"])
        plt.xticks(x, aws_region_summary_df["Region"], rotation=45, ha="right")
        plt.title("AWS Costs by Region", fontsize=16)
        plt.ylabel("USD ($)")
        plt.tight_layout(pad=2.0)
        st.pyplot(plt)
        plt.close()
    
    with col2:  # Right column for table
        # Add total row
        total_row = pd.DataFrame([{"Region": "TOTAL", "Cost": aws_region_summary_df["Cost"].sum()}])
        display_df = pd.concat([aws_region_summary_df, total_row])
        
        # Add currency symbol to the Cost column
        display_df["Cost"] = display_df["Cost"].apply(lambda x: f"${x:.2f}")
        
        # AWS region summary
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
else:
    st.info("No AWS regional cost data available.")

# Azure Region Analysis
st.subheader("Azure Regional Costs")
if azure_region_summary_df is not None and not azure_region_summary_df.empty:
    # Create two columns for side-by-side layout
    col1, col2 = st.columns(2)
    
    with col1:  # Left column for chart
        # Create region chart
        plt.figure(figsize=(8, 6))
        x = range(len(azure_region_summary_df))
        plt.bar(x, azure_region_summary_df["Cost"])
        plt.xticks(x, azure_region_summary_df["Region"], rotation=45, ha="right")
        plt.title("Azure Costs by Region", fontsize=16)
        plt.ylabel("INR")
        plt.tight_layout(pad=2.0)
        st.pyplot(plt)
        plt.close()
    
    with col2:  # Right column for table
        # Add total row
        total_row = pd.DataFrame([{"Region": "TOTAL", "Cost": azure_region_summary_df["Cost"].sum()}])
        display_df = pd.concat([azure_region_summary_df, total_row])
        
        # Add currency symbol to the Cost column
        display_df["Cost"] = display_df["Cost"].apply(lambda x: f"INR {x:.2f}")
        
        # Azure region summary
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
        st.info("Azure costs are shown in Indian Rupees (INR). $1 USD ≈ INR" + f"{1/inr_to_usd_rate:.2f}")
else:
    st.info("No Azure regional cost data available.")

# ----- AWS Section -----
st.header("AWS Costs")

# AWS Service costs
if aws_service_df is not None:
    st.subheader("AWS Service Costs")
    
    # Create two columns for side-by-side layout
    col1, col2 = st.columns(2)
    
    with col1:  # Left column for chart
        plt.figure(figsize=(8, 6))
        x = range(len(aws_service_df.head(15)))
        plt.bar(x, aws_service_df.head(15)["Cost"])
        plt.xticks(x, aws_service_df.head(15)["Service"], rotation=45, ha="right")
        plt.title("AWS Service Costs", fontsize=16)
        plt.ylabel("USD")
        plt.tight_layout(pad=2.0)
        st.pyplot(plt)
        plt.close()
    
    with col2:  # Right column for table
        # Add total row
        total_row = pd.DataFrame([{"Service": "TOTAL", "Cost": aws_service_df["Cost"].sum()}])
        display_df = pd.concat([aws_service_df, total_row])
        
        # Add currency symbol to the Cost column
        display_df["Cost"] = display_df["Cost"].apply(lambda x: f"${x:.2f}")
        
        # AWS service costs
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)

# AWS Project costs
if aws_project_df is not None:
    st.subheader("AWS Project Costs")
    
    # Create two columns for side-by-side layout
    col1, col2 = st.columns(2)
    
    with col1:  # Left column for chart
        plt.figure(figsize=(8, 6))
        x = range(len(aws_project_df))
        plt.bar(x, aws_project_df["Cost"])
        plt.xticks(x, aws_project_df["Project"], rotation=45, ha="right")
        plt.title("AWS Project Costs", fontsize=16)
        plt.ylabel("USD")
        plt.tight_layout(pad=2.0)
        st.pyplot(plt)
        plt.close()
    
    with col2:  # Right column for table
        # Add total row
        total_row = pd.DataFrame([{"Project": "TOTAL", "Cost": aws_project_df["Cost"].sum()}])
        display_df = pd.concat([aws_project_df, total_row])
        
        # Add currency symbol to the Cost column
        display_df["Cost"] = display_df["Cost"].apply(lambda x: f"${x:.2f}")
        
        # AWS project costs
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)

# AWS Untagged Resources by Region
st.subheader("AWS Untagged Resources by Region")
if aws_untagged_regions_df is not None and not aws_untagged_regions_df.empty:
    # Create two columns for side-by-side layout
    col1, col2 = st.columns(2)
    
    with col2:  # Right column for table
        # Add total row
        total_row = pd.DataFrame([{"Region": "TOTAL", "Cost": aws_untagged_regions_df["Cost"].sum()}])
        display_df = pd.concat([aws_untagged_regions_df, total_row])
        
        # Add currency symbol to the Cost column
        display_df["Cost"] = display_df["Cost"].apply(lambda x: f"${x:.2f}")
        
        # AWS untagged resources
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
    
    with col1:  # Left column for chart
        # Create a bar chart for untagged resources by region
        if not aws_untagged_regions_df.empty:
            plt.figure(figsize=(8, 6))
            x = range(len(aws_untagged_regions_df))
            plt.bar(x, aws_untagged_regions_df["Cost"])
            plt.xticks(x, aws_untagged_regions_df["Region"], rotation=45, ha="right")
            plt.title("AWS Untagged Resources by Region", fontsize=16)
            plt.ylabel("USD ($)")
            plt.tight_layout(pad=2.0)
            st.pyplot(plt)
            plt.close()
else:
    st.info("No untagged AWS resources found.")

# AWS Project Resources Breakdown
if aws_project_resources_dict:
    st.subheader("AWS Resources by Project")
    
    # Create tabs for each project
    if len(aws_project_resources_dict) > 0:
        project_tabs = st.tabs(list(aws_project_resources_dict.keys()))
        
        for i, (project, resources_df) in enumerate(aws_project_resources_dict.items()):
            with project_tabs[i]:
                if not resources_df.empty:
                    st.write(f"Resources for project: **{project}**")
                    
                    # Create a more readable view
                    display_df = resources_df[["ResourceType", "ResourceName", "Cost"]].copy()
                    
                    # Add total row
                    total_row = pd.DataFrame([{"ResourceType": "TOTAL", "ResourceName": "", "Cost": display_df["Cost"].sum()}])
                    display_df = pd.concat([display_df, total_row])
                    
                    # Add currency symbol
                    display_df["Cost"] = display_df["Cost"].apply(lambda x: f"${x:.2f}")
                    
                    # Ensure the index is reset and dropped for display
                    st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
                    
                    # Create resource type distribution chart - only use top 10 resource types by cost
                    resource_type_summary = resources_df.groupby("ResourceType")["Cost"].sum().reset_index().sort_values("Cost", ascending=False).head(20)
                    
                    # Always create chart regardless of number of resource types
                    plt.figure(figsize=(10, 6))
                    x = range(len(resource_type_summary))
                    plt.bar(x, resource_type_summary["Cost"])
                    plt.xticks(x, resource_type_summary["ResourceType"], rotation=45, ha="right")
                    plt.title(f"{project}: Cost by Resource Type", fontsize=16)
                    plt.ylabel("USD ($)")
                    plt.tight_layout(pad=2.0)
                    st.pyplot(plt)
                    plt.close()
                else:
                    st.info(f"No resources found for project: {project}")
else:
    st.info("No AWS resource breakdowns by project available.")

# ----- Azure Section -----
st.header("Azure Costs")

# Azure Resource Group costs
if azure_rg_df is not None:
    st.subheader("Azure Resource Group Costs")
    
    # Create two columns for side-by-side layout
    col1, col2 = st.columns(2)
    
    with col1:  # Left column for chart
        plt.figure(figsize=(8, 6))
        x = range(len(azure_rg_df.head(15)))
        plt.bar(x, azure_rg_df.head(15)["Cost"])
        plt.xticks(x, azure_rg_df.head(15)["ResourceGroupName"], rotation=45, ha="right")
        plt.title("Azure Resource Group Costs", fontsize=16)
        plt.ylabel("INR")
        plt.tight_layout(pad=2.0)
        st.pyplot(plt)
        plt.close()
    
    with col2:  # Right column for table
        # Add total row
        total_row = pd.DataFrame([{"ResourceGroupName": "TOTAL", "Cost": azure_rg_df["Cost"].sum(), "Currency": azure_rg_df["Currency"].iloc[0]}])
        display_df = pd.concat([azure_rg_df, total_row])
        
        # Add currency symbol to the Cost column
        display_df["Cost"] = display_df["Cost"].apply(lambda x: f"INR {x:.2f}")
        
        # Azure resource group costs
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)

# Azure Service costs
if azure_service_df is not None:
    st.subheader("Azure Service Costs")
    
    # Create two columns for side-by-side layout
    col1, col2 = st.columns(2)
    
    with col1:  # Left column for chart
        plt.figure(figsize=(8, 6))
        x = range(len(azure_service_df.head(15)))
        plt.bar(x, azure_service_df.head(15)["Cost"])
        plt.xticks(x, azure_service_df.head(15)["ServiceName"], rotation=45, ha="right")
        plt.title("Azure Service Costs", fontsize=16)
        plt.ylabel("INR")
        plt.tight_layout(pad=2.0)
        st.pyplot(plt)
        plt.close()
    
    with col2:  # Right column for table
        # Add total row
        total_row = pd.DataFrame([{"ServiceName": "TOTAL", "Cost": azure_service_df["Cost"].sum(), "Currency": azure_service_df["Currency"].iloc[0]}])
        display_df = pd.concat([azure_service_df, total_row])
        
        # Add currency symbol to the Cost column
        display_df["Cost"] = display_df["Cost"].apply(lambda x: f"INR {x:.2f}")
        
        # Azure service costs
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)

# Azure Project costs
if azure_project_df is not None:
    st.subheader("Azure Project Costs")
    
    # Create two columns for side-by-side layout
    col1, col2 = st.columns(2)
    
    with col1:  # Left column for chart
        plt.figure(figsize=(8, 6))
        x = range(len(azure_project_df))
        plt.bar(x, azure_project_df["Cost"])
        plt.xticks(x, azure_project_df["Project"], rotation=45, ha="right")
        plt.title("Azure Project Costs", fontsize=16)
        plt.ylabel("INR")
        plt.tight_layout(pad=2.0)
        st.pyplot(plt)
        plt.close()
    
    with col2:  # Right column for table
        # Add total row
        total_row = pd.DataFrame([{"Project": "TOTAL", "Cost": azure_project_df["Cost"].sum(), "Currency": azure_project_df["Currency"].iloc[0]}])
        display_df = pd.concat([azure_project_df, total_row])
        
        # Add currency symbol to the Cost column
        display_df["Cost"] = display_df["Cost"].apply(lambda x: f"INR {x:.2f}")
        
        # Azure project costs
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)

# Azure Untagged Resources by Region
st.subheader("Azure Untagged Resources by Region")
if azure_untagged_regions_df is not None and not azure_untagged_regions_df.empty:
    # Create two columns for side-by-side layout
    col1, col2 = st.columns(2)
    
    with col2:  # Right column for table
        # Add total row
        total_row = pd.DataFrame([{"Region": "TOTAL", "Cost": azure_untagged_regions_df["Cost"].sum()}])
        display_df = pd.concat([azure_untagged_regions_df, total_row])
        
        # Add currency symbol to the Cost column
        display_df["Cost"] = display_df["Cost"].apply(lambda x: f"INR {x:.2f}")
        
        # Azure untagged resources
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
    
    with col1:  # Left column for chart
        # Create a bar chart for untagged resources by region
        plt.figure(figsize=(8, 6))
        x = range(len(azure_untagged_regions_df))
        plt.bar(x, azure_untagged_regions_df["Cost"])
        plt.xticks(x, azure_untagged_regions_df["Region"], rotation=45, ha="right")
        plt.title("Azure Untagged Resources by Region", fontsize=16)
        plt.ylabel("INR")
        plt.tight_layout(pad=2.0)
        st.pyplot(plt)
        plt.close()
else:
    st.info("No untagged Azure resources found.")

# Azure Project Resources Breakdown
if azure_project_resources_dict:
    st.subheader("Azure Resources by Project")
    
    # Create tabs for each project
    if len(azure_project_resources_dict) > 0:
        project_tabs = st.tabs(list(azure_project_resources_dict.keys()))
        
        for i, (project, resources_df) in enumerate(azure_project_resources_dict.items()):
            with project_tabs[i]:
                if not resources_df.empty:
                    st.write(f"Resources for project: **{project}**")
                    
                    # Create a more readable view
                    display_df = resources_df[["ResourceType", "ResourceName", "Cost"]].copy()
                    
                    # Add total row
                    total_row = pd.DataFrame([{"ResourceType": "TOTAL", "ResourceName": "", "Cost": display_df["Cost"].sum()}])
                    display_df = pd.concat([display_df, total_row])
                    
                    # Add currency symbol
                    display_df["Cost"] = display_df["Cost"].apply(lambda x: f"INR {x:.2f}")
                    
                    # Azure project resources
                    st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
                    
                    # Create resource type distribution chart
                    resource_type_summary = resources_df.groupby("ResourceType")["Cost"].sum().reset_index().sort_values("Cost", ascending=False)
                    
                    # Always create chart regardless of number of resource types
                    plt.figure(figsize=(10, 6))
                    x = range(len(resource_type_summary))
                    plt.bar(x, resource_type_summary["Cost"])
                    plt.xticks(x, resource_type_summary["ResourceType"], rotation=45, ha="right")
                    plt.title(f"{project}: Cost by Resource Type", fontsize=16)
                    plt.ylabel("INR")
                    plt.tight_layout(pad=2.0)
                    st.pyplot(plt)
                    plt.close()
                else:
                    st.info(f"No resources found for project: {project}")
else:
    st.info("No Azure resource breakdowns by project available.")

# ----- Combined View Section -----
st.header("Combined Cloud Costs (Converted to USD)")

# Add a caption about currency conversion
st.caption(f"Azure costs have been converted from INR to USD for comparison | Exchange Rate: $1 USD = INR {1/inr_to_usd_rate:.2f}")

# Combined project costs if both are available
if aws_project_df is not None and azure_project_df is not None:
    st.subheader("Project Costs Across Clouds")
    
    # Prepare data
    aws_project_df_copy = aws_project_df.copy()
    aws_project_df_copy["Cloud"] = "AWS"
    aws_project_df_copy = aws_project_df_copy[["Project", "Cost", "Cloud"]]
    
    azure_project_df_copy = azure_project_df.copy()
    # Convert Azure costs from INR to USD for fair comparison
    azure_project_df_copy["Cost"] = azure_project_df_copy["Cost"] * inr_to_usd_rate
    azure_project_df_copy["Cloud"] = "Azure"
    azure_project_df_copy = azure_project_df_copy[["Project", "Cost", "Cloud"]]
    
    # Combine
    combined_projects = pd.concat([aws_project_df_copy, azure_project_df_copy])
    
    # Pivot for comparison
    pivot_df = combined_projects.pivot_table(
        index="Project", 
        columns="Cloud", 
        values="Cost", 
        aggfunc="sum",
        fill_value=0
    ).reset_index()
    
    # Calculate totals
    pivot_df["Total"] = pivot_df["AWS"] + pivot_df["Azure"]
    pivot_df = pivot_df.sort_values("Total", ascending=False)
    
    # Add totals row
    total_row = pd.DataFrame([{
        "Project": "TOTAL",
        "AWS": aws_total,
        "Azure": azure_total_usd,  # Already converted to USD
        "Total": combined_total
    }])
    display_df = pd.concat([pivot_df, total_row])
    
    # Format currency values with 2 decimal places and $ symbol
    for col in ["AWS", "Azure", "Total"]:
        display_df[col] = display_df[col].apply(lambda x: f"${x:.2f}")
    
    # Create two columns for side-by-side layout
    col1, col2 = st.columns(2)
    
    with col1:  # Left column for chart
        # Bar chart comparison
        top_projects = pivot_df.head(10)
        fig, ax = plt.subplots(figsize=(8, 6))
        width = 0.35
        x = range(len(top_projects))
        ax.bar([i - width/2 for i in x], top_projects["AWS"], width, label="AWS", color="#FF9900")
        ax.bar([i + width/2 for i in x], top_projects["Azure"], width, label="Azure (Converted to USD)", color="#0089D6")
        ax.set_ylabel("Cost ($)")
        ax.set_xticks(x)
        ax.set_xticklabels(top_projects["Project"], rotation=45, ha="right")
        ax.legend()
        ax.set_title("Top 10 Projects by Cost (USD)", fontsize=16)
        plt.tight_layout()
        st.pyplot(fig)
    
    with col2:  # Right column for table
        # Display combined table with clear USD labels
        st.subheader("Project Costs Across Clouds Table (USD)")
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)

st.caption(f"Report generated on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}") 