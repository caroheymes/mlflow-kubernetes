
# MLflow on Kubernetes - Local Deployment

A complete guide to deploy MLflow on a local Kubernetes cluster (minikube) with PostgreSQL backend and persistent storage for artifacts.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Project Structure](#project-structure)
- [Local Deployment](#local-deployment)
- [Testing](#testing)
- [Next Steps](#next-steps)
- [Troubleshooting](#troubleshooting)

## Prerequisites

### Required Tools
- **Minikube** (v1.30+) - Local Kubernetes cluster
- **kubectl** (v1.28+) - Kubernetes CLI
- **Docker** (v20.10+) - Container runtime
- **Python** (v3.9+) - For testing scripts
- **WSL2** (Windows) - Windows Subsystem for Linux (recommended for Windows users)



### Installation
```bash
# Install Minikube
# Download from: https://minikube.sigs.k8s.io/docs/start/

# Install kubectl
# Download from: https://kubernetes.io/docs/tasks/tools/

# Install Docker Desktop
# Download from: https://www.docker.com/products/docker-desktop

# Install Python dependencies
pip install mlflow scikit-learn
```

### Minikube config
``bash
Get-WindowsOptionalFeature -FeatureName Microsoft-Hyper-V -Online

minikube start `
  --driver=hyperv `
  --cpus=4 `
  --memory=4096 `
  --disk-size=50gb `
  --kubernetes-version=latest
```

## Project Structure


mlflow_v2/
├── README.md
├── .gitignore
├── secret.yaml.example          # Template for secrets
├── test_mlflow.py               # Test script
│
├── kubernetes/
│   ├── postgres/
│   │   ├── postgres-pv.yaml     # PersistentVolume
│   │   ├── postgres-pvc.yaml    # PersistentVolumeClaim
│   │   ├── postgres-deploy.yaml # Deployment
│   │   └── postgres-service.yaml # Service
│   │
│   ├── mlflow/
│   │   ├── artifacts-pv.yaml
│   │   ├── artifacts-pvc.yaml
│   │   ├── mlflow-deploy.yaml
│   │   └── mlflow-service.yaml
│
└── mlflow-data/                 # Local data storage
    ├── postgres/                # PostgreSQL data
    └── artifacts/               # MLflow artifacts


## Local Deployment

### Step 1: Create Local Data Directories

```powershell
# Windows (PowerShell)
mkdir C:\mlflow_v2\mlflow-data\postgres
mkdir C:\mlflow_v2\mlflow-data\artifacts
```

### Step 2: Create Kubernetes Secrets

Copy `secret.yaml.example` to `secret.yaml` and keep it in `.gitignore`:

```powershell
cp secret.yaml.example secret.yaml
# Edit secret.yaml with your base64-encoded credentials
```

Apply the secret:
```powershell
kubectl apply -f secret.yaml
```

### Step 3: Deploy PostgreSQL and MLflow

```powershell
cd C:\mlflow_v2

# Apply all Kubernetes resources
kubectl apply -f kubernetes/

# Verify deployment
kubectl get pods
kubectl get svc
```

Both `postgres-xxx` and `mlflow-xxx` pods should be in `Running` status.

### Step 4: Access MLflow

Forward the MLflow service to your local machine:

```powershell
kubectl port-forward svc/mlflow-service 5000:5000
```

Open your browser and go to:
```
http://localhost:5000
```

## Testing

### Run the Test Script

Once MLflow is running and port-forward is active, execute:

```powershell
python test_mlflow.py
```

**Expected output:**
```
✅ Run enregistré ! Accuracy: 1.00
🏃 View run gentle-cub-158 at: http://localhost:5000/#/experiments/1/runs/...
🧪 View experiment at: http://localhost:5000/#/experiments/1
```

### Verify Artifacts

Check that artifacts were created:

```powershell
ls C:\mlflow_v2\mlflow-data\artifacts\
```

You should see folders like `1/`, `2/`, etc. (experiment IDs).

## Next Steps

- [ ] Deploy to GCP (GKE + Cloud SQL + Cloud Storage)
- [ ] Add model registry
- [ ] Implement CI/CD pipeline
- [ ] Add authentication & authorization
- [ ] Scale to multiple MLflow replicas

## Troubleshooting

### Pod in CrashLoopBackOff

Check logs:
```powershell
kubectl logs <pod-name>
```

Common issues:
- PostgreSQL not accessible: verify `postgres-service` is running
- Volume mount issues: check PersistentVolume paths

### Connection Refused on localhost:5000

Ensure port-forward is active:
```powershell
kubectl port-forward svc/mlflow-service 5000:5000
```

### CORS Errors in Browser

Verify `MLFLOW_DISABLE_SECURITY=true` is set in `mlflow-deploy.yaml` (for development only).



