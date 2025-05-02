import streamlit as st
import os
import json
import datetime
import pandas as pd
import matplotlib.pyplot as plt
import boto3
from azure.identity import AzureCliCredential
import requests
import time
import io
import base64
from matplotlib.backends.backend_pdf import PdfPages
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
import tempfile

# Page configuration
st.set_page_config(
    page_title="Cloud Cost Dashboard",
    page_icon="💰",
    layout="wide"
)

# App title
st.title("Cloud Cost Dashboard")

# Hardcoded configuration values
# Date range (last 7 days)
today = datetime.date.today()
end_date = today - datetime.timedelta(days=1)
start_date = today - datetime.timedelta(days=7)
start_iso = start_date.isoformat()
end_iso = end_date.isoformat()

# AWS Configuration
aws_profile = "cost-report"
aws_metrics = ["AmortizedCost"]  # Can include "BlendedCost", "UnblendedCost" if needed
include_all_aws_cost_types = True

# Azure Configuration
azure_subscription = "1cbd30d4-5a1f-4cb1-839f-5b8b66807c1d"

# Display date range information
st.info(f"Displaying cloud costs from {start_date} to {end_date}")

# Tabs for different views
tab1, tab2, tab3 = st.tabs(["AWS Costs", "Azure Costs", "Combined View"])

# ----- AWS Cost Functions -----
def get_aws_costs(start_date, end_date, profile_name, metrics, include_all_cost_types=True):
    """Fetch AWS cost data"""
    try:
        client = boto3.Session(profile_name=profile_name).client("ce")
        
        # Base parameters
        params = {
            "TimePeriod": {"Start": start_date, "End": end_date},
            "Granularity": "DAILY",
            "Metrics": metrics
        }
        
        # Add filter for all cost types if requested
        if include_all_cost_types:
            params["Filter"] = {
                "Dimensions": {
                    "Key": "RECORD_TYPE",
                    "Values": [
                        "Usage", "Tax", "Refund", "Credit", "Discount", "DiscountedUsage", 
                        "SavingsPlanNegation", "SavingsPlanUpfrontFee", "SavingsPlanRecurringFee"
                    ]
                }
            }
        
        # Get total costs
        total_response = client.get_cost_and_usage(**params)
        
        # Get costs by service
        service_params = params.copy()
        service_params["GroupBy"] = [{"Type": "DIMENSION", "Key": "SERVICE"}]
        service_response = client.get_cost_and_usage(**service_params)
        
        # Get costs by project tag
        project_params = params.copy()
        project_params["GroupBy"] = [{"Type": "TAG", "Key": "Project"}]
        project_response = client.get_cost_and_usage(**project_params)
        
        return {
            "total": total_response,
            "service": service_response,
            "project": project_response
        }
    except Exception as e:
        st.error(f"Error fetching AWS costs: {str(e)}")
        return None

def process_aws_data(aws_data, metric="AmortizedCost"):
    """Process AWS cost data for display"""
    if not aws_data:
        return None, None, None
    
    # Process total costs by day
    daily_totals = []
    for time_result in aws_data["total"]["ResultsByTime"]:
        day = time_result["TimePeriod"]["Start"]
        cost = float(time_result["Total"][metric]["Amount"])
        daily_totals.append({"Date": day, "Cost": cost})
    
    daily_df = pd.DataFrame(daily_totals)
    
    # Process service costs
    service_data = []
    for time_result in aws_data["service"]["ResultsByTime"]:
        for group in time_result.get("Groups", []):
            service = group["Keys"][0]
            cost = float(group["Metrics"][metric]["Amount"])
            service_data.append({"Service": service, "Cost": cost})
    
    service_df = pd.DataFrame(service_data).groupby("Service").sum().reset_index()
    service_df = service_df.sort_values("Cost", ascending=False)
    
    # Process project costs
    project_data = []
    for time_result in aws_data["project"]["ResultsByTime"]:
        for group in time_result.get("Groups", []):
            project = group["Keys"][0]
            cost = float(group["Metrics"][metric]["Amount"])
            project_data.append({"Project": project, "Cost": cost})
    
    project_df = pd.DataFrame(project_data).groupby("Project").sum().reset_index()
    project_df = project_df.sort_values("Cost", ascending=False)
    
    return daily_df, service_df, project_df

# ----- Azure Cost Functions -----
def get_azure_token():
    """Get Azure authentication token"""
    try:
        cred = AzureCliCredential()
        token = cred.get_token("https://management.azure.com/.default").token
        return token
    except Exception as e:
        st.error(f"Error getting Azure token: {str(e)}")
        return None

def get_azure_costs(token, subscription_id, start_date, end_date):
    """Fetch Azure cost data"""
    if not token:
        return None
    
    try:
        # Base parameters
        url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query?api-version=2023-03-01"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Dimensions to query
        dimensions = [
            {"type": "Dimension", "name": "ResourceGroupName"},
            {"type": "Dimension", "name": "ServiceName"},
            {"type": "TagKey", "name": "project"}
        ]
        
        results = {}
        
        for dim in dimensions:
            body = {
                "type": "Usage",
                "timeframe": "Custom",
                "timePeriod": {
                    "from": f"{start_date}T00:00:00Z",
                    "to": f"{end_date}T23:59:59Z"
                },
                "dataset": {
                    "granularity": "Daily",
                    "aggregation": {
                        "totalCost": {
                            "name": "PreTaxCost",
                            "function": "Sum"
                        }
                    },
                    "grouping": [dim]
                }
            }
            
            resp = requests.post(url, headers=headers, json=body)
            resp.raise_for_status()
            results[dim["name"]] = resp.json()
            
            # Be nice to the API
            time.sleep(1)
        
        return results
    except Exception as e:
        st.error(f"Error fetching Azure costs: {str(e)}")
        return None

def process_azure_data(azure_data):
    """Process Azure cost data for display"""
    if not azure_data:
        return None, None, None
    
    # Process resource group data
    rg_data = azure_data.get("ResourceGroupName")
    if rg_data:
        cols = [c["name"] for c in rg_data["properties"]["columns"]]
        rows = rg_data["properties"]["rows"]
        rg_df = pd.DataFrame(rows, columns=cols)
        rg_df = rg_df.rename(columns={c: "Cost" for c in rg_df.columns if "cost" in c.lower()})
        rg_df = rg_df[["ResourceGroupName", "Cost", "Currency"]]
        rg_df["Cost"] = pd.to_numeric(rg_df["Cost"])
        rg_df = rg_df.sort_values("Cost", ascending=False)
    else:
        rg_df = None
    
    # Process service data
    service_data = azure_data.get("ServiceName")
    if service_data:
        cols = [c["name"] for c in service_data["properties"]["columns"]]
        rows = service_data["properties"]["rows"]
        service_df = pd.DataFrame(rows, columns=cols)
        service_df = service_df.rename(columns={c: "Cost" for c in service_df.columns if "cost" in c.lower()})
        service_df = service_df[["ServiceName", "Cost", "Currency"]]
        service_df["Cost"] = pd.to_numeric(service_df["Cost"])
        service_df = service_df.sort_values("Cost", ascending=False)
    else:
        service_df = None
    
    # Process project tag data
    project_data = azure_data.get("project")
    if project_data:
        cols = [c["name"] for c in project_data["properties"]["columns"]]
        rows = project_data["properties"]["rows"]
        project_df = pd.DataFrame(rows, columns=cols)
        
        # Handle different formats
        if "TagValue" in project_df.columns:
            project_df = project_df.rename(columns={"TagValue": "Project"})
            project_df = project_df.rename(columns={c: "Cost" for c in project_df.columns if "cost" in c.lower()})
            project_df = project_df[["Project", "Cost", "Currency"]]
        else:
            project_df = project_df.rename(columns={c: "Cost" for c in project_df.columns if "cost" in c.lower()})
            project_col = [c for c in project_df.columns if c != "Cost" and c != "Currency"][0]
            project_df = project_df.rename(columns={project_col: "Project"})
            project_df = project_df[["Project", "Cost", "Currency"]]
        
        project_df["Project"] = project_df["Project"].fillna("Untagged")
        project_df["Cost"] = pd.to_numeric(project_df["Cost"])
        project_df = project_df.sort_values("Cost", ascending=False)
    else:
        project_df = None
    
    return rg_df, service_df, project_df

# ----- PDF Generation Functions -----
def fig_to_buffer(fig):
    """Convert a matplotlib figure to a bytes buffer"""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', dpi=300)
    buf.seek(0)
    return buf

def dataframe_to_table_data(df, include_total=True):
    """Convert a pandas DataFrame to a list of lists for ReportLab Table"""
    # Convert DataFrame to list of lists
    data = [df.columns.tolist()]
    for _, row in df.iterrows():
        data.append(row.tolist())
    
    # Format values (especially floats/costs)
    for i in range(1, len(data)):
        for j in range(len(data[i])):
            if isinstance(data[i][j], float):
                data[i][j] = f"${data[i][j]:.2f}"
    
    return data

def save_as_pdf(aws_data, azure_data, combined_data, filename="cloud_costs_report.pdf"):
    """Generate a comprehensive PDF report with all cloud cost data"""
    
    # Initialize variables that might be referenced later but aren't defined
    aws_account_df = None
    azure_subscription_df = None
    azure_resource_group_df = None
    gcp_data = None
    gcp_project_df = None
    gcp_service_df = None
    
    # Extract all dataframes and figures
    aws_daily_df, aws_service_df, aws_project_df = aws_data
    azure_rg_df, azure_service_df, azure_project_df = azure_data
    aws_total, azure_total, combined_total, combined_project_df, aws_vs_azure_fig, project_comparison_fig = combined_data
    
    # Create a PDF document
    doc = SimpleDocTemplate(filename, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()
    
    # Title
    elements.append(Paragraph(f"Cloud Cost Report: {start_date} to {end_date}", styles["Title"]))
    elements.append(Spacer(1, 12))
    
    # Summary section
    elements.append(Paragraph("Cost Summary", styles["Heading1"]))
    elements.append(Spacer(1, 6))
    
    summary_data = [
        ["Cloud Provider", "Total Cost"],
        ["AWS", f"${aws_total:.2f}"],
        ["Azure", f"${azure_total:.2f}"],
        ["Combined Total", f"${combined_total:.2f}"]
    ]
    summary_table = Table(summary_data)
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 12))
    
    # AWS vs Azure pie chart
    if aws_vs_azure_fig:
        buf = fig_to_buffer(aws_vs_azure_fig)
        img = Image(buf, width=400, height=300)
        elements.append(Paragraph("Cost Distribution by Cloud Provider", styles["Heading2"]))
        elements.append(img)
        elements.append(Spacer(1, 12))
    
    # AWS Section
    if aws_data is not None and len(aws_data) > 0:
        elements.append(PageBreak())
        elements.append(Paragraph("AWS Costs", styles["Heading1"]))
        
        # AWS Account Costs
        if aws_account_df is not None and len(aws_account_df) > 0:
            elements.append(Paragraph("AWS Account Costs", styles["Heading2"]))
            
            # Create horizontal bar chart for costs by account
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.barh(aws_account_df["Account"], aws_account_df["Cost"])
            ax.set_xlabel("Cost ($)")
            plt.tight_layout()
            
            buf = fig_to_buffer(fig)
            img = Image(buf, width=450, height=250)
            elements.append(img)
            plt.close(fig)
            
            # Add table with total row safely
            total_dict = {"Account": "TOTAL", "Cost": aws_account_df["Cost"].sum()}
            if "Currency" in aws_account_df.columns:
                total_dict["Currency"] = aws_account_df["Currency"].iloc[0]
            
            aws_account_display = aws_account_df.copy()
            # Ensure all required columns are present but don't add those that aren't
            columns_to_include = ["Account", "Cost"]
            if "Currency" in aws_account_df.columns:
                columns_to_include.append("Currency")
            
            aws_account_display = aws_account_df[columns_to_include].copy()
            
            # Add the total row
            total_row = pd.DataFrame([total_dict])
            aws_account_with_total = pd.concat([aws_account_display, total_row])
            
            account_table_data = dataframe_to_table_data(aws_account_with_total)
            account_table = Table(account_table_data)
            account_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            elements.append(Spacer(1, 12))
            elements.append(account_table)
            elements.append(Spacer(1, 12))
        
        # AWS Project Costs
        if aws_project_df is not None and len(aws_project_df) > 0:
            elements.append(Paragraph("AWS Project Costs", styles["Heading2"]))
            
            # Create horizontal bar chart for costs by project
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.barh(aws_project_df["Project"], aws_project_df["Cost"])
            ax.set_xlabel("Cost ($)")
            plt.tight_layout()
            
            buf = fig_to_buffer(fig)
            img = Image(buf, width=450, height=250)
            elements.append(img)
            plt.close(fig)
            
            # Add table with total row
            # Add total row safely
            total_dict = {"Project": "TOTAL", "Cost": aws_project_df["Cost"].sum()}
            if "Currency" in aws_project_df.columns:
                total_dict["Currency"] = aws_project_df["Currency"].iloc[0]
            
            aws_project_display = aws_project_df.copy()
            # Ensure all required columns are present but don't add those that aren't
            columns_to_include = ["Project", "Cost"]
            if "Currency" in aws_project_df.columns:
                columns_to_include.append("Currency")
            
            aws_project_display = aws_project_df[columns_to_include].copy()
            
            # Add the total row
            total_row = pd.DataFrame([total_dict])
            aws_project_with_total = pd.concat([aws_project_display, total_row])
            
            project_table_data = dataframe_to_table_data(aws_project_with_total)
            project_table = Table(project_table_data)
            project_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            elements.append(Spacer(1, 12))
            elements.append(project_table)
            elements.append(Spacer(1, 12))
        
        # AWS Service Costs
        if aws_service_df is not None and len(aws_service_df) > 0:
            elements.append(Paragraph("AWS Service Costs", styles["Heading2"]))
            
            # Create horizontal bar chart for costs by service
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.barh(aws_service_df.head(10)["Service"], aws_service_df.head(10)["Cost"])
            ax.set_xlabel("Cost ($)")
            plt.tight_layout()
            
            buf = fig_to_buffer(fig)
            img = Image(buf, width=450, height=250)
            elements.append(img)
            plt.close(fig)
            
            # Add table with total row
            # Add total row safely
            total_dict = {"Service": "TOTAL", "Cost": aws_service_df["Cost"].sum()}
            if "Currency" in aws_service_df.columns:
                total_dict["Currency"] = aws_service_df["Currency"].iloc[0]
            
            aws_service_display = aws_service_df.copy()
            # Ensure all required columns are present but don't add those that aren't
            columns_to_include = ["Service", "Cost"]
            if "Currency" in aws_service_df.columns:
                columns_to_include.append("Currency")
            
            aws_service_display = aws_service_df[columns_to_include].copy()
            
            # Add the total row
            total_row = pd.DataFrame([total_dict])
            aws_service_with_total = pd.concat([aws_service_display, total_row])
            
            service_table_data = dataframe_to_table_data(aws_service_with_total)
            service_table = Table(service_table_data)
            service_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            elements.append(Spacer(1, 12))
            elements.append(service_table)
            elements.append(Spacer(1, 12))
    
    # Page break before Azure section
    elements.append(Paragraph("", styles["Normal"]))
    elements.append(Spacer(1, 50))  # Force page break
    
    # Azure Section
    if azure_data is not None and len(azure_data) > 0:
        elements.append(PageBreak())
        elements.append(Paragraph("Azure Costs", styles["Heading1"]))
        
        # Azure Subscription Costs
        if azure_subscription_df is not None and len(azure_subscription_df) > 0:
            elements.append(Paragraph("Azure Subscription Costs", styles["Heading2"]))
            
            # Create horizontal bar chart for costs by subscription
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.barh(azure_subscription_df["Subscription"], azure_subscription_df["Cost"])
            ax.set_xlabel("Cost ($)")
            plt.tight_layout()
            
            buf = fig_to_buffer(fig)
            img = Image(buf, width=450, height=250)
            elements.append(img)
            plt.close(fig)
            
            # Add table with total row
            # Add total row safely
            total_dict = {"Subscription": "TOTAL", "Cost": azure_subscription_df["Cost"].sum()}
            if "Currency" in azure_subscription_df.columns:
                total_dict["Currency"] = azure_subscription_df["Currency"].iloc[0]
            
            azure_subscription_display = azure_subscription_df.copy()
            # Ensure all required columns are present but don't add those that aren't
            columns_to_include = ["Subscription", "Cost"]
            if "Currency" in azure_subscription_df.columns:
                columns_to_include.append("Currency")
            
            azure_subscription_display = azure_subscription_df[columns_to_include].copy()
            
            # Add the total row
            total_row = pd.DataFrame([total_dict])
            azure_subscription_with_total = pd.concat([azure_subscription_display, total_row])
            
            subscription_table_data = dataframe_to_table_data(azure_subscription_with_total)
            subscription_table = Table(subscription_table_data)
            subscription_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            elements.append(Spacer(1, 12))
            elements.append(subscription_table)
            elements.append(Spacer(1, 12))
        
        # Azure Service Costs
        if azure_service_df is not None and len(azure_service_df) > 0:
            elements.append(Paragraph("Azure Service Costs", styles["Heading2"]))
            
            # Create horizontal bar chart for costs by service
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.barh(azure_service_df.head(10)["Service"], azure_service_df.head(10)["Cost"])
            ax.set_xlabel("Cost ($)")
            plt.tight_layout()
            
            buf = fig_to_buffer(fig)
            img = Image(buf, width=450, height=250)
            elements.append(img)
            plt.close(fig)
            
            # Add table with total row
            # Add total row safely
            total_dict = {"Service": "TOTAL", "Cost": azure_service_df["Cost"].sum()}
            if "Currency" in azure_service_df.columns:
                total_dict["Currency"] = azure_service_df["Currency"].iloc[0]
            
            azure_service_display = azure_service_df.copy()
            # Ensure all required columns are present but don't add those that aren't
            columns_to_include = ["Service", "Cost"]
            if "Currency" in azure_service_df.columns:
                columns_to_include.append("Currency")
            
            azure_service_display = azure_service_df[columns_to_include].copy()
            
            # Add the total row
            total_row = pd.DataFrame([total_dict])
            azure_service_with_total = pd.concat([azure_service_display, total_row])
            
            service_table_data = dataframe_to_table_data(azure_service_with_total)
            service_table = Table(service_table_data)
            service_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            elements.append(Spacer(1, 12))
            elements.append(service_table)
            elements.append(Spacer(1, 12))
        
        # Azure Resource Group Costs
        if azure_resource_group_df is not None and len(azure_resource_group_df) > 0:
            elements.append(Paragraph("Azure Resource Group Costs", styles["Heading2"]))
            
            # Create horizontal bar chart for costs by resource group
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.barh(azure_resource_group_df.head(10)["ResourceGroup"], azure_resource_group_df.head(10)["Cost"])
            ax.set_xlabel("Cost ($)")
            plt.tight_layout()
            
            buf = fig_to_buffer(fig)
            img = Image(buf, width=450, height=250)
            elements.append(img)
            plt.close(fig)
            
            # Add table with total row
            # Add total row safely
            total_dict = {"ResourceGroup": "TOTAL", "Cost": azure_resource_group_df["Cost"].sum()}
            if "Currency" in azure_resource_group_df.columns:
                total_dict["Currency"] = azure_resource_group_df["Currency"].iloc[0]
            
            azure_resource_group_display = azure_resource_group_df.copy()
            # Ensure all required columns are present but don't add those that aren't
            columns_to_include = ["ResourceGroup", "Cost"]
            if "Currency" in azure_resource_group_df.columns:
                columns_to_include.append("Currency")
            
            azure_resource_group_display = azure_resource_group_df[columns_to_include].copy()
            
            # Add the total row
            total_row = pd.DataFrame([total_dict])
            azure_resource_group_with_total = pd.concat([azure_resource_group_display, total_row])
            
            resource_group_table_data = dataframe_to_table_data(azure_resource_group_with_total)
            resource_group_table = Table(resource_group_table_data)
            resource_group_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            elements.append(Spacer(1, 12))
            elements.append(resource_group_table)
            elements.append(Spacer(1, 12))
    
    # Page break before Combined section
    elements.append(Paragraph("", styles["Normal"]))
    elements.append(Spacer(1, 50))  # Force page break
    
    # Combined Section
    elements.append(Paragraph("Combined Cloud Costs", styles["Heading1"]))
    elements.append(Spacer(1, 6))
    
    # Combined Costs
    if combined_data is not None and len(combined_data) > 0:
        elements.append(PageBreak())
        elements.append(Paragraph("Combined Cloud Costs by Project", styles["Heading2"]))
        
        # Create combined costs chart
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.barh(combined_data.head(10)["Project"], combined_data.head(10)["Cost"])
        ax.set_xlabel("Cost ($)")
        plt.tight_layout()
        
        buf = fig_to_buffer(fig)
        img = Image(buf, width=450, height=250)
        elements.append(img)
        plt.close(fig)
        
        # Add combined costs table
        # Add total row safely
        total_dict = {"Project": "TOTAL", "Cost": combined_data["Cost"].sum()}
        if "Currency" in combined_data.columns:
            total_dict["Currency"] = combined_data["Currency"].iloc[0]
        if "Cloud" in combined_data.columns:
            total_dict["Cloud"] = "All"
        
        combined_display = combined_data.copy()
        # Ensure all required columns are present but don't add those that aren't
        columns_to_include = ["Project", "Cost"]
        if "Currency" in combined_data.columns:
            columns_to_include.append("Currency")
        if "Cloud" in combined_data.columns:
            columns_to_include.append("Cloud")
        
        combined_display = combined_data[columns_to_include].copy()
        
        # Add the total row
        total_row = pd.DataFrame([total_dict])
        combined_with_total = pd.concat([combined_display, total_row])
        
        combined_data = dataframe_to_table_data(combined_with_total)
        combined_table = Table(combined_data)
        combined_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(Spacer(1, 12))
        elements.append(combined_table)
        elements.append(Spacer(1, 12))
    
    # GCP Section
    if gcp_data is not None and len(gcp_data) > 0:
        elements.append(PageBreak())
        elements.append(Paragraph("Google Cloud Costs", styles["Heading1"]))
        
        # GCP Project Costs
        if gcp_project_df is not None and len(gcp_project_df) > 0:
            elements.append(Paragraph("Google Cloud Project Costs", styles["Heading2"]))
            
            # Create horizontal bar chart for costs by project
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.barh(gcp_project_df["Project"], gcp_project_df["Cost"])
            ax.set_xlabel("Cost ($)")
            plt.tight_layout()
            
            buf = fig_to_buffer(fig)
            img = Image(buf, width=450, height=250)
            elements.append(img)
            plt.close(fig)
            
            # Add table with total row
            # Add total row safely
            total_dict = {"Project": "TOTAL", "Cost": gcp_project_df["Cost"].sum()}
            if "Currency" in gcp_project_df.columns:
                total_dict["Currency"] = gcp_project_df["Currency"].iloc[0]
            
            gcp_project_display = gcp_project_df.copy()
            # Ensure all required columns are present but don't add those that aren't
            columns_to_include = ["Project", "Cost"]
            if "Currency" in gcp_project_df.columns:
                columns_to_include.append("Currency")
            
            gcp_project_display = gcp_project_df[columns_to_include].copy()
            
            # Add the total row
            total_row = pd.DataFrame([total_dict])
            gcp_project_with_total = pd.concat([gcp_project_display, total_row])
            
            project_table_data = dataframe_to_table_data(gcp_project_with_total)
            project_table = Table(project_table_data)
            project_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            elements.append(Spacer(1, 12))
            elements.append(project_table)
            elements.append(Spacer(1, 12))
        
        # GCP Service Costs
        if gcp_service_df is not None and len(gcp_service_df) > 0:
            elements.append(Paragraph("Google Cloud Service Costs", styles["Heading2"]))
            
            # Create horizontal bar chart for costs by service
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.barh(gcp_service_df.head(10)["Service"], gcp_service_df.head(10)["Cost"])
            ax.set_xlabel("Cost ($)")
            plt.tight_layout()
            
            buf = fig_to_buffer(fig)
            img = Image(buf, width=450, height=250)
            elements.append(img)
            plt.close(fig)
            
            # Add table with total row
            # Add total row safely
            total_dict = {"Service": "TOTAL", "Cost": gcp_service_df["Cost"].sum()}
            if "Currency" in gcp_service_df.columns:
                total_dict["Currency"] = gcp_service_df["Currency"].iloc[0]
            
            gcp_service_display = gcp_service_df.copy()
            # Ensure all required columns are present but don't add those that aren't
            columns_to_include = ["Service", "Cost"]
            if "Currency" in gcp_service_df.columns:
                columns_to_include.append("Currency")
            
            gcp_service_display = gcp_service_df[columns_to_include].copy()
            
            # Add the total row
            total_row = pd.DataFrame([total_dict])
            gcp_service_with_total = pd.concat([gcp_service_display, total_row])
            
            service_table_data = dataframe_to_table_data(gcp_service_with_total)
            service_table = Table(service_table_data)
            service_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            elements.append(Spacer(1, 12))
            elements.append(service_table)
            elements.append(Spacer(1, 12))
    
    # Build the PDF
    doc.build(elements)
    return filename

def get_pdf_download_link(pdf_filename, link_text="Download PDF Report"):
    """Generate a link to download the PDF file"""
    with open(pdf_filename, "rb") as f:
        pdf_data = f.read()
    b64_pdf = base64.b64encode(pdf_data).decode()
    href = f'<a href="data:application/pdf;base64,{b64_pdf}" download="{pdf_filename}" target="_blank">{link_text}</a>'
    return href

# ----- Main app logic -----
with st.spinner("Fetching cloud cost data..."):
    # Get AWS costs
    aws_data = None
    aws_daily_df = aws_service_df = aws_project_df = None
    
    aws_data = get_aws_costs(start_iso, end_iso, aws_profile, aws_metrics, include_all_aws_cost_types)
    if aws_data:
        aws_daily_df, aws_service_df, aws_project_df = process_aws_data(aws_data, aws_metrics[0])
    
    # Get Azure costs
    azure_data = None
    azure_rg_df = azure_service_df = azure_project_df = None
    
    azure_token = get_azure_token()
    if azure_token:
        azure_data = get_azure_costs(azure_token, azure_subscription, start_iso, end_iso)
        if azure_data:
            azure_rg_df, azure_service_df, azure_project_df = process_azure_data(azure_data)

# ----- AWS Costs Tab -----
with tab1:
    st.header("AWS Costs")
    
    if aws_data and aws_daily_df is not None:
        # Display total
        total_aws_cost = aws_daily_df["Cost"].sum()
        st.metric("Total AWS Cost", f"${total_aws_cost:.2f}")
        
        # Daily trend
        st.subheader("Daily AWS Costs")
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.bar(aws_daily_df["Date"], aws_daily_df["Cost"])
        ax.set_ylabel("Cost ($)")
        ax.set_xticklabels(aws_daily_df["Date"], rotation=45)
        st.pyplot(fig)
        
        # Services breakdown
        if aws_service_df is not None:
            st.subheader("Costs by Service")
            top_services = aws_service_df.head(15)  # Top 15 services
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.barh(top_services["Service"], top_services["Cost"])
            ax.set_xlabel("Cost ($)")
            st.pyplot(fig)
            
            # Services table
            st.dataframe(aws_service_df.reset_index(drop=True), use_container_width=True, hide_index=True)
        
        # Project tag breakdown
        if aws_project_df is not None:
            st.subheader("Costs by Project Tag")
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.barh(aws_project_df["Project"], aws_project_df["Cost"])
            ax.set_xlabel("Cost ($)")
            st.pyplot(fig)
            
            # Add total row
            total_row = pd.DataFrame([{"Project": "TOTAL", "Cost": aws_project_df["Cost"].sum()}])
            display_df = pd.concat([aws_project_df, total_row])
            
            # Project table
            st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
    else:
        st.info("No AWS cost data available. Please check your AWS profile configuration.")

# ----- Azure Costs Tab -----
with tab2:
    st.header("Azure Costs")
    
    if azure_data and azure_rg_df is not None:
        # Display total
        total_azure_cost = azure_rg_df["Cost"].sum()
        st.metric("Total Azure Cost", f"${total_azure_cost:.2f}")
        
        # Resource Group breakdown
        st.subheader("Costs by Resource Group")
        fig, ax = plt.subplots(figsize=(10, 6))
        top_rgs = azure_rg_df.head(15)  # Top 15 resource groups
        ax.barh(top_rgs["ResourceGroupName"], top_rgs["Cost"])
        ax.set_xlabel("Cost ($)")
        st.pyplot(fig)
        
        # Add total row
        total_row = pd.DataFrame([{"ResourceGroupName": "TOTAL", "Cost": azure_rg_df["Cost"].sum(), "Currency": azure_rg_df["Currency"].iloc[0]}])
        display_df = pd.concat([azure_rg_df, total_row])
        
        # Resource Group table
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
        
        # Service breakdown
        if azure_service_df is not None:
            st.subheader("Costs by Service")
            fig, ax = plt.subplots(figsize=(10, 6))
            top_services = azure_service_df.head(15)  # Top 15 services
            ax.barh(top_services["ServiceName"], top_services["Cost"])
            ax.set_xlabel("Cost ($)")
            st.pyplot(fig)
            
            # Add total row
            total_row = pd.DataFrame([{"ServiceName": "TOTAL", "Cost": azure_service_df["Cost"].sum(), "Currency": azure_service_df["Currency"].iloc[0]}])
            display_df = pd.concat([azure_service_df, total_row])
            
            # Service table
            st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
        
        # Project tag breakdown
        if azure_project_df is not None:
            st.subheader("Costs by Project Tag")
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.barh(azure_project_df["Project"], azure_project_df["Cost"])
            ax.set_xlabel("Cost ($)")
            st.pyplot(fig)
            
            # Add total row
            total_row = pd.DataFrame([{"Project": "TOTAL", "Cost": azure_project_df["Cost"].sum(), "Currency": azure_project_df["Currency"].iloc[0]}])
            display_df = pd.concat([azure_project_df, total_row])
            
            # Project table
            st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
    else:
        st.info("No Azure cost data available. Please check your Azure subscription configuration.")

# ----- Combined View Tab -----
with tab3:
    st.header("Combined Cloud Costs")
    
    # Calculate totals
    aws_total = aws_daily_df["Cost"].sum() if aws_daily_df is not None else 0
    azure_total = azure_rg_df["Cost"].sum() if azure_rg_df is not None else 0
    combined_total = aws_total + azure_total
    
    # Display combined total
    st.metric("Total Cloud Costs", f"${combined_total:.2f}")
    
    # Pie chart of AWS vs Azure
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.pie([aws_total, azure_total], 
           labels=["AWS", "Azure"], 
           autopct='%1.1f%%',
           colors=['#FF9900', '#0089D6'])
    ax.set_title("Cost Distribution by Cloud Provider")
    aws_azure_pie_chart = fig  # Save for PDF
    st.pyplot(fig)
    
    # Combined project costs if both are available
    project_comparison_chart = None
    combined_pivot_df = None
    
    if aws_project_df is not None and azure_project_df is not None:
        st.subheader("Project Costs Across Clouds")
        
        # Prepare data
        aws_project_df_copy = aws_project_df.copy()
        aws_project_df_copy["Cloud"] = "AWS"
        aws_project_df_copy = aws_project_df_copy[["Project", "Cost", "Cloud"]]
        
        azure_project_df_copy = azure_project_df.copy()
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
        combined_pivot_df = pivot_df  # Save for PDF
        
        # Add totals row
        total_row = pd.DataFrame([{
            "Project": "TOTAL",
            "AWS": aws_total,
            "Azure": azure_total,
            "Total": combined_total
        }])
        display_df = pd.concat([pivot_df, total_row])
        
        # Display table
        st.dataframe(display_df.reset_index(drop=True), use_container_width=True, hide_index=True)
        
        # Bar chart comparison
        top_projects = pivot_df.head(10)
        fig, ax = plt.subplots(figsize=(12, 6))
        width = 0.35
        x = range(len(top_projects))
        ax.bar([i - width/2 for i in x], top_projects["AWS"], width, label="AWS", color="#FF9900")
        ax.bar([i + width/2 for i in x], top_projects["Azure"], width, label="Azure", color="#0089D6")
        ax.set_ylabel("Cost ($)")
        ax.set_xticks(x)
        ax.set_xticklabels(top_projects["Project"], rotation=45, ha="right")
        ax.legend()
        ax.set_title("Top 10 Projects by Cost")
        project_comparison_chart = fig  # Save for PDF
        st.pyplot(fig)

# Add a download button for Excel
col1, col2 = st.columns(2)

with col1:
    if st.button("Download Data as Excel"):
        # Create Excel file
        with pd.ExcelWriter("cloud_costs.xlsx") as writer:
            if aws_service_df is not None:
                aws_service_df.to_excel(writer, sheet_name="AWS Services", index=False)
            if aws_project_df is not None:
                aws_project_df.to_excel(writer, sheet_name="AWS Projects", index=False)
            if azure_rg_df is not None:
                azure_rg_df.to_excel(writer, sheet_name="Azure Resource Groups", index=False)
            if azure_project_df is not None:
                azure_project_df.to_excel(writer, sheet_name="Azure Projects", index=False)
        
        # Read the Excel file into bytes for download
        with open("cloud_costs.xlsx", "rb") as f:
            excel_data = f.read()
        
        # Create a download button
        st.download_button(
            label="Download Excel File",
            data=excel_data,
            file_name="cloud_costs.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

with col2:
    if st.button("Generate PDF Report"):
        with st.spinner("Generating PDF report... This may take a moment."):
            # Create temp file for PDF
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            pdf_filename = temp_file.name
            
            # Gather data for PDF
            aws_data = (aws_daily_df, aws_service_df, aws_project_df)
            azure_data = (azure_rg_df, azure_service_df, azure_project_df)
            combined_data = (aws_total, azure_total, combined_total, combined_pivot_df, aws_azure_pie_chart, project_comparison_chart)
            
            # Generate PDF
            save_as_pdf(aws_data, azure_data, combined_data, pdf_filename)
            
            # Create download link
            pdf_link = get_pdf_download_link(pdf_filename, "Download PDF Report")
            st.markdown(pdf_link, unsafe_allow_html=True)
            
            st.success("PDF report generated successfully!")