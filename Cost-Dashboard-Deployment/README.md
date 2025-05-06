# Cost Dashboard

## Minio

1. Install Minio in the cost-dashboard namespace

helm install minio oci://registry-1.docker.io/bitnamicharts/minio -n cost-dashboard -f values.yaml

helm repo add minio-operator https://operator.min.io

<!-- helm install -n cost-dashboard operator minio-operator/operator -->

helm install -n cost-dashboard --values values-operator.yaml cost-minio minio-operator/tenant

# minio | minio123

2. Build the dockerfile

```bash
docker buildx build --platform linux/amd64 --no-cache -t nstarx.azurecr.io/cost-dashboard:{version} . -f Dockerfile --push
```

## Directory Structure

The deployment consists of multiple components:

### `/deployments/app`
- Dashboard application (`static_cloud_cost_report.py`)
- Streamlit-based visualization of AWS and Azure costs
- Build with its own Dockerfile

### `/deployments/cron_scripts`
- Scripts for collecting cost data from AWS and Azure
- `get_aws_costs_cron.py` - Collects AWS cost data
- `get_azure_costs_cron.py` - Collects Azure cost data
- `execute.sh` - Main entrypoint script with retry logic
- Configured as a daily job via `cronjob` file

### `/deployments/cronjob`
- Kubernetes deployment configurations for the cost collection jobs
- `cronjob.yaml` - Defines the deployment with proper secrets and environment variables

## Deployment Instructions

1. Set up Kubernetes secrets for AWS and Azure credentials:
```bash
kubectl create secret generic aws-credentials \
  --namespace=cost-dashboard \
  --from-literal=aws_access_key_id=YOUR_ACCESS_KEY \
  --from-literal=aws_secret_access_key=YOUR_SECRET_KEY \
  --from-literal=aws_region=YOUR_REGION

kubectl create secret generic azure-credentials \
  --namespace=cost-dashboard \
  --from-literal=azure_tenant_id=YOUR_TENANT_ID \
  --from-literal=azure_client_id=YOUR_CLIENT_ID \
  --from-literal=azure_client_secret=YOUR_CLIENT_SECRET
```

2. Apply the Kubernetes configuration:
```bash
kubectl apply -f deployments/cronjob/cronjob.yaml -n cost-dashboard
```

3. Build and deploy the dashboard app:
```bash
cd deployments/app
docker buildx build --platform linux/amd64 -t nstarx.azurecr.io/cost-dashboard:{version} . -f Dockerfile --push
# Then deploy using your preferred Kubernetes method
```