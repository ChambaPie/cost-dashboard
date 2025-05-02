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