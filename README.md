# Cost Dashboard

A cloud cost management solution that collects, analyzes, and visualizes AWS and Azure spending data. The dashboard provides insights into costs by service, region, project tags, and resource groups.

## Project Structure

### Cost-Dashboard-Local

Contains a local version of the deployment for development and testing:
- Python scripts for AWS and Azure cost collection
- Streamlit dashboard app for visualization
- Configuration files and templates

### Cost-Dashboard-Deployment

Contains production-ready deployment resources:
- Kubernetes deployment configurations
- CronJob scripts for automated data collection
- Dockerfiles and container setup
- MinIO integration for data storage

## Quick Start

1. Set up the required AWS and Azure credentials
2. Deploy the dashboard using the Kubernetes manifests
3. Configure the data collection CronJobs

See detailed documentation in the respective directories.


