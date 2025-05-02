# Azure Cost Management Script

This Python script helps visualize the cost breakdown of your Azure account, with a focus on Azure Kubernetes Service (AKS) clusters.

## Features

- Connect to your Azure account
- List all available subscriptions
- Find all AKS clusters in a subscription
- Fetch cost data for a specified timeframe
- Visualize overall Azure costs by service
- Analyze and visualize AKS-specific costs by resource group

## Requirements

- Python 3.6+
- Azure CLI installed and configured with appropriate permissions
- Required Python packages (listed in requirements.txt)

## Installation

1. Clone this repository or download the files
2. Install the required Python packages:

```bash
pip install -r requirements.txt
```

3. Make sure you're logged in to Azure CLI:

```bash
az login
```

## Usage

Run the script with:

```bash
python azure_cost_analyzer.py
```

The script will:
1. Connect to your Azure account and list available subscriptions
2. Use the first subscription (you can modify the script to select a different one)
3. Fetch AKS clusters in the subscription
4. Get cost data for the current month
5. Generate visualizations for overall costs and AKS-specific costs

## Visualization Output

The script generates two visualization files:
- `azure_cost_breakdown.png`: Overall cost breakdown by Azure service
- `aks_cost_breakdown.png`: AKS cost breakdown by resource group

## Customization

You can modify the script to:
- Change the timeframe for cost analysis (MonthToDate, LastMonth, Last3Months)
- Change the granularity of cost data (Daily, Monthly)
- Add more detailed analysis of specific services
- Filter costs by tags or other dimensions

## Permissions Required

Your Azure account needs to have the following permissions:
- Cost Management Reader role or higher at the subscription level
- Reader role for AKS resources 