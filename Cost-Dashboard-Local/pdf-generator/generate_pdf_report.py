#!/usr/bin/env python3
import os, json, datetime
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.backends.backend_pdf import PdfPages

# ───────── helpers ─────────
def today_stamp():
    return datetime.datetime.now().strftime("%d-%m-%Y")

def last_week():
    today = datetime.date.today()
    end   = today
    start = today - datetime.timedelta(days=7)
    return start.isoformat(), end.isoformat()

def load_df(path):
    with open(path) as f:
        d = json.load(f)
    cols = [c["name"] for c in d["properties"]["columns"]]
    rows = d["properties"]["rows"]
    return pd.DataFrame(rows, columns=cols)

def add_table(pdf, title, df, highlight_total=False, col_widths=None):
    fig, ax = plt.subplots(figsize=(8,11))
    ax.axis('off')
    ax.set_title(title, pad=20)
    tbl = ax.table(cellText=df.values,
                   colLabels=df.columns,
                   loc='center',
                   colWidths=col_widths)
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1,1.5)
    if highlight_total:
        for i in range(len(df.columns)):
            cell = tbl[len(df)-1, i]
            cell.set_facecolor("lightgray")
            cell.set_text_props(weight='bold')
    pdf.savefig(fig); plt.close(fig)

# ───────── main report ─────────
def build_report():
    stamp        = today_stamp()
    azure_dir    = f"azure-cost-reports-{stamp}"
    aws_dir      = f"aws-cost-reports-{stamp}"
    start, end   = last_week()
    pdf_path     = f"cloud-cost-report_{start}_{end}.pdf"

    with PdfPages(pdf_path) as pdf:
        # Cover
        fig = plt.figure(figsize=(8,11))
        plt.text(0.5,0.6,"Weekly Cloud Cost Report",ha="center",va="center",size=24)
        plt.text(0.5,0.4,f"{start} to {end}",ha="center",va="center",size=16)
        plt.axis('off'); pdf.savefig(fig); plt.close(fig)

        # Utility: iterate Azure then AWS
        for cloud, folder in [("Azure", azure_dir), ("AWS", aws_dir)]:
            if not os.path.isdir(folder):
                continue
            # charts
            pngs = [os.path.join(folder,f) for f in os.listdir(folder)
                    if f.endswith(".png") and start in f and end in f]
            pngs.sort()
            for p in pngs:
                img = mpimg.imread(p)
                fig = plt.figure(figsize=(8,11))
                plt.imshow(img); plt.axis('off')
                pdf.savefig(fig); plt.close(fig)

            # tables
            if cloud == "Azure":
                # Resource Group table
                json_path = os.path.join(folder,
                            f"raw_ResourceGroupName_{start}_{end}.json")
                if os.path.exists(json_path):
                    df = load_df(json_path)
                    df = df.rename(columns={c:'Cost' for c in df.columns if 'cost' in c.lower()})
                    df['Cost'] = df['Cost'].round(2)
                    total = df['Cost'].sum().round(2)
                    df_display = df[['Cost','ResourceGroupName','Currency']]
                    df_display.loc[len(df_display)] = [total,'TOTAL',df_display['Currency'].iloc[0]]
                    add_table(pdf,"Azure Cost by Resource Group",df_display,highlight_total=True,
                              col_widths=[0.2,0.6,0.2])
                
                # Project tag table
                project_json = os.path.join(folder,
                              f"raw_project_{start}_{end}.json")
                if os.path.exists(project_json):
                    # FIXED: Use project_json not json_path
                    df = load_df(project_json)
                    df = df.rename(columns={c:'Cost' for c in df.columns if 'cost' in c.lower()})
                    
                    # For TagKey/TagValue format
                    if 'TagKey' in df.columns and 'TagValue' in df.columns:
                        df['TagValue'] = df['TagValue'].fillna('Untagged')
                        df_display = df[['Cost','TagValue','Currency']]
                        df_display = df_display.rename(columns={'TagValue':'Project'})
                    else:
                        # Check for project column with various possible names
                        project_col = None
                        for col in ['project', 'Project', 'PROJECT']:
                            if col in df.columns:
                                project_col = col
                                break
                                
                        if project_col:
                            df_display = df[['Cost', project_col, 'Currency']]
                            df_display = df_display.rename(columns={project_col: 'Project'})
                        else:
                            # If no project column found, use any column that's not Cost or Currency
                            non_cost_cols = [c for c in df.columns if c != 'Cost' and c != 'Currency']
                            if non_cost_cols:
                                df_display = df[['Cost', non_cost_cols[0], 'Currency']]
                                df_display = df_display.rename(columns={non_cost_cols[0]: 'Project'})
                            else:
                                print(f"⚠️ No suitable project column found in {project_json}")
                                continue
                    
                    df_display['Cost'] = df_display['Cost'].round(2)
                    total = df_display['Cost'].sum().round(2)
                    df_display.loc[len(df_display)] = [total,'TOTAL',df_display['Currency'].iloc[0]]
                    add_table(pdf,"Azure Cost by Tag: Project",df_display,highlight_total=True,
                              col_widths=[0.2,0.6,0.2])

            if cloud == "AWS":
                print(f"\n🔍 Looking for AWS data in {folder}")
                
                # First check if AWS directory exists and has files
                if not os.path.isdir(folder):
                    print(f"⚠️ AWS directory {folder} does not exist")
                    continue
                
                aws_files = os.listdir(folder)
                print(f"📁 AWS files found: {aws_files}")
                
                # Function to load AWS data with fallback methods
                def load_aws_data(filepath):
                    print(f"📊 Attempting to load AWS data from {filepath}")
                    try:
                        # First try: Check if data is in format from get_aws_costs.py
                        with open(filepath) as f:
                            data = json.load(f)
                            
                        # Special handling for AWS Cost Explorer data format
                        if "ResultsByTime" in data:
                            print("✓ Detected AWS Cost Explorer format")
                            
                            result_data = []
                            for time_period in data["ResultsByTime"]:
                                for group in time_period.get("Groups", []):
                                    key = group["Keys"][0]
                                    cost = float(group["Metrics"]["AmortizedCost"]["Amount"])
                                    result_data.append({"Key": key, "Cost": cost})
                            
                            return pd.DataFrame(result_data)
                        # For other formats, try standard load
                        else:
                            print("⚠️ Not in AWS Cost Explorer format, trying standard load...")
                            return load_df(filepath)
                    except Exception as e:
                        print(f"⚠️ Error loading AWS data: {e}")
                        # Last resort: try to load it directly
                        try:
                            return pd.read_json(filepath)
                        except Exception as nested_e:
                            print(f"❌ All loading methods failed: {nested_e}")
                            return None

                # Table 1 – by Service
                # Look for service data with case-insensitive matching
                service_json = os.path.join(folder, f"raw_SERVICE_{start}_{end}.json")
                print(f"🔍 Looking for AWS Service file: {service_json}")
                if os.path.exists(service_json):
                    print(f"✓ Found AWS Service file")
                    df = load_aws_data(service_json)
                    if df is not None and not df.empty:
                        print(f"✓ Loaded service data with columns: {df.columns.tolist()}")
                        
                        # Try to identify cost column
                        cost_cols = [c for c in df.columns if any(term in c.lower() for term in ['cost', 'amount', 'amortizedcost'])]
                        if cost_cols:
                            cost_col = cost_cols[0]
                        elif 'Key' in df.columns and len(df.columns) >= 2:  # Handle format from resp_to_df
                            # The AWS get_aws_costs.py script creates a simple Key/Cost DataFrame
                            cost_col = 'Cost'
                        elif len(df.columns) >= 2:  # Assume second column if at least 2 columns
                            cost_col = df.columns[1]
                        else:
                            print("❌ Could not identify cost column in service data")
                            cost_col = df.columns[0]  # Just use first column as fallback
                            
                        print(f"📊 Using '{cost_col}' as cost column")
                        
                        # Identify service column
                        if 'Key' in df.columns:  # Handle format from resp_to_df
                            service_col = 'Key'
                            print(f"📊 Using 'Key' column for service names")
                        else:
                            non_cost_cols = [c for c in df.columns if c != cost_col and not any(term in c.lower() for term in ['cost', 'currency'])]
                            if non_cost_cols:
                                service_col = non_cost_cols[0]
                                print(f"📊 Using '{service_col}' as service column")
                            else:
                                print("❌ Could not identify service column")
                                service_col = df.columns[0]  # Fallback to first column
                        
                        # Proceed with table creation
                        try:
                            # Convert cost to numeric and round
                            df = df.rename(columns={cost_col: 'Cost'})
                            df['Cost'] = pd.to_numeric(df['Cost'], errors='coerce').fillna(0).round(2)
                            
                            # Sort and take top services
                            df = df.sort_values(by='Cost', ascending=False).head(30)
                            
                            # Calculate total
                            total = df['Cost'].sum().round(2)
                            
                            # Ensure Currency column exists
                            if 'Currency' not in df.columns:
                                df['Currency'] = 'USD'
                            
                            # Create display dataframe
                            df_display = pd.DataFrame()
                            df_display['Cost'] = df['Cost']
                            df_display['Service'] = df[service_col] if service_col in df.columns else 'Unknown'
                            df_display['Currency'] = df['Currency'] if 'Currency' in df.columns else 'USD'
                            
                            # Add total row
                            df_display.loc[len(df_display)] = [total, 'TOTAL', df_display['Currency'].iloc[0]]
                            
                            print(f"✓ Created service table with {len(df_display)-1} rows plus total")
                            add_table(pdf, "AWS Cost by Service", df_display, highlight_total=True,
                                    col_widths=[0.2, 0.6, 0.2])
                        except Exception as e:
                            print(f"❌ Error creating service table: {str(e)}")
                else:
                    print(f"❌ AWS Service file not found: {service_json}")
                
                # Table 2 – by Project tag
                # Look for project data with case-insensitive matching
                project_json = os.path.join(folder, f"raw_Project_{start}_{end}.json")
                print(f"🔍 Looking for AWS Project file: {project_json}")
                if os.path.exists(project_json):
                    print(f"✓ Found AWS Project file")
                    df = load_aws_data(project_json)
                    if df is not None and not df.empty:
                        print(f"✓ Loaded project data with columns: {df.columns.tolist()}")
                        
                        # Try to identify cost column
                        cost_cols = [c for c in df.columns if any(term in c.lower() for term in ['cost', 'amount', 'amortizedcost'])]
                        if cost_cols:
                            cost_col = cost_cols[0]
                        elif 'Key' in df.columns and len(df.columns) >= 2:  # Handle format from resp_to_df
                            # The AWS get_aws_costs.py script creates a simple Key/Cost DataFrame
                            cost_col = 'Cost'
                        elif len(df.columns) >= 2:  # Assume second column if at least 2 columns
                            cost_col = df.columns[1]
                        else:
                            print("❌ Could not identify cost column in project data")
                            cost_col = df.columns[0]  # Just use first column as fallback
                            
                        print(f"📊 Using '{cost_col}' as cost column")
                        
                        # Identify project column
                        if 'Key' in df.columns:  # Handle format from resp_to_df
                            project_col = 'Key'
                            print(f"📊 Using 'Key' column for project names")
                        else:
                            non_cost_cols = [c for c in df.columns if c != cost_col and not any(term in c.lower() for term in ['cost', 'currency'])]
                            if non_cost_cols:
                                project_col = non_cost_cols[0]
                                print(f"📊 Using '{project_col}' as project column")
                            else:
                                print("❌ Could not identify project column")
                                project_col = df.columns[0]  # Fallback to first column
                        
                        # Proceed with table creation
                        try:
                            # Convert cost to numeric and round
                            df = df.rename(columns={cost_col: 'Cost'})
                            df['Cost'] = pd.to_numeric(df['Cost'], errors='coerce').fillna(0).round(2)
                            
                            # Handle nulls
                            df[project_col] = df[project_col].fillna('Untagged')
                            
                            # Ensure Currency column exists
                            if 'Currency' not in df.columns:
                                df['Currency'] = 'USD'
                            
                            # Calculate total
                            total = df['Cost'].sum().round(2)
                            
                            # Create display dataframe
                            df_display = pd.DataFrame()
                            df_display['Cost'] = df['Cost']
                            df_display['Project'] = df[project_col] if project_col in df.columns else 'Unknown'
                            df_display['Currency'] = df['Currency'] if 'Currency' in df.columns else 'USD'
                            
                            # Add total row
                            df_display.loc[len(df_display)] = [total, 'TOTAL', df_display['Currency'].iloc[0]]
                            
                            print(f"✓ Created project table with {len(df_display)-1} rows plus total")
                            add_table(pdf, "AWS Cost by Tag: Project", df_display, highlight_total=True,
                                    col_widths=[0.2, 0.6, 0.2])
                        except Exception as e:
                            print(f"❌ Error creating project table: {str(e)}")
                else:
                    print(f"❌ AWS Project file not found: {project_json}")

    print(f"✅ PDF created: {pdf_path}")

if __name__ == "__main__":
    build_report()





