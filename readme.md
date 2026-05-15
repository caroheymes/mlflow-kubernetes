# Déploiement : Distributed ML (Ray + MLflow) sur GKE

Ce projet documente la mise en place d'une infrastructure complète pour le Machine Learning distribué utilisant **Ray** pour le calcul, **MLflow** pour le tracking, **Cloud SQL** pour le stockage des métriques et **GCS** pour les artefacts.

```text

+-----------------------------------------------------------------------------------+
|                            GOOGLE CLOUD PLATFORM (GCP)                            |
|                                                                                   |
|  +---------------------------+       +-------------------+   +-----------------+  |
|  |     SECRET MANAGER        |       |    CLOUD SQL      |   |  CLOUD STORAGE  |  |
|  | (mlflow-db-password)      |       |  (PostgreSQL)     |   |   (GCS Bucket)  |  |
|  +-------------+-------------+       +--------+----------+   +--------+--------+  |
|                |                              ^                       ^           |
|                | IAM (Lecteur de secret)      | IAM (Client SQL)      | IAM       |
|                v                              |                       | (Admin)   |
|===================================================================================|
|                            KUBERNETES CLUSTER (GKE)                               |
|                                                                                   |
|  +----------------------+                                                         |
|  | EXTERNAL SECRETS     |                                                         |
|  | (eso-ksa)            |                                                         |
|  | Transforme le secret |                                                         |
|  | GCP en secret K8s    |                                                         |
|  +---------+------------+                                                         |
|            |                                                                      |
|            v (Injecte le mot de passe)                                            |
|  +-------------------------------------------------------------+                  |
|  |                 MLFLOW DEPLOYMENT (Pod)                     |                  |
|  |                                                             |                  |
|  |  +--------------------+       +--------------------------+  |                  |
|  |  |   MLFLOW SERVER    |       |     CLOUD SQL PROXY      |--+----------------+ |
|  |  | (Interface & API)  |<======| (Tunnel chiffré local)   |  |                  |
|  |  +---------+----------+       +--------------------------+  |                  |
|  +------------+------------------------------------------------+                  |
|               |                                ^                                  |
|               v                                | (Envoi des métriques             |
|   [ mlflow-test-service ]                      |  via HTTP port 5000)             |
|   [     (Port 5000)     ]                      |                                  |
|               ^                                |                                  |
|===============|================================|==================================|
|               |                                |                                  |
|               |    RAY CLUSTER (Géré par KubeRay Operator)                        |
|               |                                |                                  |
|  +------------+-------------+                  |                                  |
|  |      RAY HEAD NODE       |------------------+                                  |
|  |  (Chef d'orchestre)      |                                                     |
|  |  Lance ton script Python |                                                     |
|  +----+------------------+--+                                                     |
|       |                  |                                                        |
|       v (Distribue)      v (Distribue)                                            |
|  +---------+        +---------+                                                   |
|  | WORKER 1|        | WORKER 2|  <-- C'est ici que les CPU/GPU travaillent        |
|  | PyTorch |        | PyTorch |                |
|  +----+----+        +----+----+                                                   |
|       |                  |                                                        |
|       +------------------+--------------------------------------------------------+
|             (Écriture des modèles / Checkpoints de l'entraînement)                |
+-----------------------------------------------------------------------------------+
```
---

## Sommaire
1. [Initialisation GCloud & GKE](#1-initialisation-gcloud--gke)  
2. [Stockage & Base de données (GCS & Cloud SQL)](#2-stockage--base-de-données-gcs--cloud-sql)  
3. [Docker & Environnement Python](#3-docker--environnement-python)  
4. [Sécurité & Workload Identity](#4-sécurité--workload-identity)  
5. [Déploiement MLflow](#5-déploiement-mlflow)  
6. [Déploiement Ray (KubeRay)](#6-déploiement-ray-kuberay)  
7. [Opérations & Maintenance](#7-opérations--maintenance)  
8. [Gestion des coûts (Shutdown / Wake‑up)](#8-gestion-des-coûts-shutdown--wake‑up)

---
## 1. Initialisation GCloud & GKE

### Configuration GCloud & APIs
On prépare l'environnement et on active les services nécessaires (Kubernetes, SQL, Storage, Secret Manager).

```PowerShell

# Connexion et configuration du projet
gcloud auth login
gcloud config set project [PROJECT_ID]

# Activation des APIs nécessaires
gcloud services enable `
    container.googleapis.com `
    cloudbuild.googleapis.com `
    compute.googleapis.com `
    sqladmin.googleapis.com `
    storage-api.googleapis.com `
    iam.googleapis.com `
    cloudresourcemanager.googleapis.com `
    servicenetworking.googleapis.com `
    secretmanager.googleapis.com `
    artifactregistry.googleapis.com
```

### Création du Cluster GKE
Création d'un cluster avec 3 nœuds standard pour supporter Ray et MLflow.

```PowerShell
gcloud container clusters create ray-gpu-cluster `
    --zone europe-west1-b `
    --num-nodes 3 `
    --machine-type e2-standard-4 `
    --workload-pool=[PROJECT_ID].svc.id.goog

# Connexion kubectl
gcloud container clusters get-credentials ray-gpu-cluster --zone europe-west1-b
```

---

## 2. Stockage & Base de données (GCS & Cloud SQL)

### Google Cloud Storage (Artefacts)


```PowerShell
gcloud storage buckets create gs://mlflow-artifacts-[ID] `
    --location=europe-west1 `
    --uniform-bucket-level-access

gcloud projects add-iam-policy-binding ray-distributed-ml `
    --member="serviceAccount:mlflow-gsa@ray-distributed-ml.iam.gserviceaccount.com" `
    --role="roles/storage.admin"
```
### Cloud SQL (PostgreSQL via IP Privée)
On crée la plage d'IP et on autorise le VPC Peering (le pont réseau) 
```PowerShell
# 1. Réserver l'IP
gcloud compute addresses create google-managed-services-default `
    --global --purpose=VPC_PEERING --prefix-length=16 --network=default
# 2. Connecter le pont
gcloud services vpc-peerings connect `
    --service=servicenetworking.googleapis.com --ranges=google-managed-services-default --network=default

# Création de l'instance
gcloud sql instances create mlflow-postgres `
    --database-version=POSTGRES_14 --tier=db-f1-micro `
    --region=europe-west1 --network=default --no-assign-ip

# Création de l'utilisateur
# dans la console on va sur secret manager et on crée un secret mlflow-db-password et un mot de passe
#  ou
# [System.Text.Encoding]::UTF8.GetBytes("votre_mot_de_passe") | gcloud secrets create mlflow-db-password --data-file=-

# droits IAM de lire les secrets
gcloud projects add-iam-policy-binding ray-distributed-ml `
>>   --member="user:[email_admin]" `
>>   --role="roles/secretmanager.secretAccessor"

# droits IAM de gérer cloudSQL
gcloud projects add-iam-policy-binding ray-distributed-ml `
  --member="user:[email_admin]" `
  --role="roles/cloudsql.admin"

# en cas de doute s'assurer qu'on est bien sur le bon projet 
gcloud config set project ray-distributed-ml


# attendre 1 ou 2 minutes pour la propagation  

# On récupère le secret proprement
$DB_PASS = gcloud secrets versions access latest --secret="mlflow-db-password"

# On crée l'utilisateur
gcloud sql users create mlflow_user --instance=mlflow-postgres --password=$DB_PASS

# on crée la base de données MLFlow
gcloud sql databases create mlflow_db --instance=mlflow-postgres
```
## 3. Docker & Environnement Python
### Dockerfile

```Dockerfile
# Si ML simple
# FROM rayproject/ray:nightly-py312
# SI deep learning
FROM rayproject/ray:nightly-py312-gpu

# A ajuster suisvant les besoins
RUN pip install --no-cache-dir \
  imbalanced-learn \
  pandas \
  mlflow \
  pandas \
  scikit-learn \
  skore \
  "protobuf<=3.20.3" \
  tensorboardX \
  "ray[train]" \
  "ray[tune]" \
  "ray[data]"\
  joblib\
  torch \
  torchvision \
  optuna \
  numpy

COPY fraud_detection.py /home/ray/
COPY train.py /home/ray/ 
COPY detection_object.py /home/ray/ 


```
### Build et Push sur GCR
```PowerShell
gcloud auth configure-docker
gcloud builds submit --tag gcr.io/[PROJECT_ID]/ray-training:latest .
```

## 4. Sécurité & Workload Identity
### Comptes de Service Google (GSA)
```PowerShell
# Création du GSA pour MLflow et Ray
gcloud iam service-accounts create mlflow-gsa

# Droits GCS et Cloud SQL
gcloud projects add-iam-policy-binding [PROJECT_ID] `
    --member="serviceAccount:mlflow-gsa@[PROJECT_ID].iam.gserviceaccount.com" `
    --role="roles/storage.objectAdmin"
```
### Liaison Kubernetes (KSA)
```PowerShell
kubectl create serviceaccount mlflow-ksa

gcloud iam service-accounts add-iam-policy-binding mlflow-gsa@[PROJECT_ID].iam.gserviceaccount.com `
    --role roles/iam.workloadIdentityUser `
    --member "serviceAccount:[PROJECT_ID].svc.id.goog[default/mlflow-ksa]"

kubectl annotate serviceaccount mlflow-ksa `
    iam.gke.io/gcp-service-account=mlflow-gsa@[PROJECT_ID].iam.gserviceaccount.com
```

### Gestion des secrets (External Secrets Operator)
Mise en place de la synchronisation automatisée des secrets depuis Google Secret Manager vers Kubernetes.



création du fichier eso-mlflow-config.yaml
```yaml
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: gcp-secret-store
  namespace: default
spec:
  provider:
    gcpsm:
      projectID: "ray-distributed-ml" 
      auth:
        workloadIdentity:
          clusterLocation: "europe-west1-b"
          clusterName: "ray-gpu-cluster"
          serviceAccountRef:
            name: eso-ksa
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: mlflow-db-password-sync
  namespace: default
spec:
  refreshInterval: "1h"
  secretStoreRef:
    name: gcp-secret-store
    kind: SecretStore
  target:
    name: mlflow-db-password 
    creationPolicy: Owner
  data:
  - secretKey: password 
    remoteRef:
      key: mlflow-db-password 
      version: latest
```
Installation via Helm

```powershell
helm repo add external-secrets https://charts.external-secrets.io
helm repo update

helm upgrade --install external-secrets external-secrets/external-secrets -n external-secrets --create-namespace --set installCRDs=true

#verif
kubectl get crds | Select-String "external-secrets"
```


Configuration IAM pour l'Opérateur

```powershell
# GSA et droits pour ESO
gcloud iam service-accounts create eso-gsa --display-name="External Secrets Operator"

gcloud projects add-iam-policy-binding [PROJECT_ID] `
    --member="serviceAccount:eso-gsa@[PROJECT_ID].iam.gserviceaccount.com" `
    --role="roles/secretmanager.secretAccessor"

# KSA et Workload Identity pour ESO
kubectl create serviceaccount eso-ksa -n default

gcloud iam service-accounts add-iam-policy-binding eso-gsa@[PROJECT_ID].iam.gserviceaccount.com `
    --role roles/iam.workloadIdentityUser `
    --member "serviceAccount:[PROJECT_ID].svc.id.goog[default/eso-ksa]"

kubectl annotate serviceaccount eso-ksa `
    iam.gke.io/gcp-service-account=eso-gsa@[PROJECT_ID].iam.gserviceaccount.com

kubectl apply -f eso-mlflow-config.yaml

#si bug
kubectl api-resources --api-group=external-secrets.io
```



## 5. Déploiement MLflow
Note de sécurité : Le sidecar proxy n'utilise plus de fichier de clé JSON grâce à Workload Identity. Le mot de passe de la DB est injecté automatiquement par ESO.

### Manifest MLflow (mlflow-deployment.yaml) 
```YAML
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mlflow-test
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mlflow-test
  template:
    metadata:
      labels:
        app: mlflow-test
    spec:
      serviceAccountName: mlflow-ksa
      containers:
      # ===== MLflow Container =====
      - name: mlflow
        image: ghcr.io/mlflow/mlflow:v3.12.0-full
        command: 
        - "sh"
        - "-c"
        - |
          mlflow server \
            --host 0.0.0.0 \
            --port 5000 \
            --backend-store-uri "postgresql://mlflow_user:${DB_PASSWORD}@127.0.0.1:5432/mlflow_db" \
            --default-artifact-root "${MLFLOW_DEFAULT_ARTIFACT_ROOT}" \
            --disable-security-middleware
        ports:
        - containerPort: 5000
          name: http
        envFrom:
        - configMapRef:
            name: mlflow-config
        env:
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: mlflow-db-password # <-- Généré par ESO
              key: password
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "1"
        livenessProbe:
          httpGet:
            path: /
            port: 5000
            httpHeaders:
            - name: Host
              value: "localhost"
          initialDelaySeconds: 90
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /
            port: 5000
            httpHeaders:
            - name: Host
              value: "localhost"
          initialDelaySeconds: 60
          periodSeconds: 5

      # ===== Cloud SQL Auth Proxy Sidecar =====
      - name: cloud-sql-proxy
        image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.10.1
        args:
          - "--port=5432"
          - "--private-ip"
          - "distributed-ml-496012:europe-west1:mlflow-postgres"
        # Plus de volumeMounts nécessaires grâce à Workload Identity
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "200m"

---
apiVersion: v1
kind: Service
metadata:
  name: mlflow-test-service
  namespace: default
spec:
  type: LoadBalancer
  selector:
    app: mlflow-test
  ports:
  - name: http
    protocol: TCP
    port: 5000
    targetPort: 5000
```

## 6. Déploiement Ray (KubeRay)

### Manifest Ray Cluster (`ray-cluster.yaml`)

KubeRay installe les Custom Resource Definitions
```powershell
# 1. Ajouter le dépôt de KubeRay à Helm
helm repo add kuberay https://ray-project.github.io/kuberay-helm/
helm repo update

# 2. Installer l'opérateur KubeRay
helm install kuberay-operator kuberay/kuberay-operator --version 1.1.0
```
 
On a créé un cluster sur --machine-type e2-standard-4 avec 4 vCPUs, 16 Go RAM

On peut avoir besoin de patate pour faire du deep learning donc on active les gpus en ajoutant un pool de nœuds GPU au cluster

```yaml
apiVersion: ray.io/v1
kind: RayCluster
metadata:
  name: raycluster-kuberay
spec:
  rayVersion: '2.9.0'
  headGroupSpec:
    rayStartParams:
      dashboard-host: '0.0.0.0'
    template:
      spec:
        serviceAccountName: mlflow-ksa # Pour l'accès GCS
        containers:
          - name: ray-head
            image:  gcr.io/ray-distributed-ml/ray-training:latest # <-- vient du docker build
            imagePullPolicy: Always  # <-- force l'utilisation de la dernière image docker
            ports:
              - containerPort: 6379
                name: gcs
              - containerPort: 8265
                name: dashboard
              - containerPort: 10001
                name: client
            resources:
              limits:
                cpu: "1"
                memory: "4Gi"
              requests:
                cpu: "1"
                memory: "4Gi"
  workerGroupSpecs:
    - replicas: 1
      groupName: small-group
      rayStartParams: {}
      template:
        spec:
          serviceAccountName: mlflow-ksa # Pour l'accès GCS
          containers:
            - name: ray-worker
              image:  gcr.io/ray-distributed-ml/ray-training:latest # <-- vient du docker build
              resources:
               #----------si CPU
                # limits:
                #   cpu: "500m" # 
                #   memory: "2Gi"
                #   
                # requests:
                #   cpu: "500m" 
                #   memory: "2Gi" 
                #  
                #------si deep learning
                limits:
                  cpu: "4" # "4" si deep learning
                  memory: "16Gi" # "16Gi"si DL
                  nvidia.com/gpu: "1" # <-- A ACTIVER SI DEEP LEARNING
                requests:
                  cpu: "4"  # "4" si deep learning
                  memory: "16Gi" # "16Gi"si DL
                  nvidia.com/gpu: "1" # <-- A ACTIVER SI DEEP LEARNING
          nodeSelector:
            cloud.google.com/gke-nodepool: gpu-pool
          tolerations:
          - key: "nvidia.com/gpu"
            operator: "Exists"
            effect: "NoSchedule"    
```

**Activer les gpus sur le cluster**
```powershell
gcloud container node-pools create gpu-pool `
   --cluster=ray-gpu-cluster `
   --machine-type=n1-standard-8 `
   --accelerator="type=nvidia-tesla-t4,count=1" `
   --zone=europe-west1-b `
   --node-locations=europe-west1-c `
   --num-nodes=1

kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded-latest.yaml


```

## 7. Opérations & Maintenance

### Accès aux interfaces
```PowerShell
# Dashboard Ray
kubectl port-forward service/raycluster-kuberay-head-svc 8265:8265
dahsboard ray disponible sur http://localhost:8265

# MLflow UI
kubectl port-forward service/mlflow-test-service 5000:5000


si crash
kubectl rollout restart deployment mlflow-test

# Récupérer l'IP du LoadBalancer
kubectl get svc mlflow-test-service

# si entraînement sur GPU, après modif du ray-cluster
kubectl delete pod raycluster-kuberay-worker-small-group-p896m
kubectl get pods -w

```
### Exécution de scripts

```PowerShell
# Trouver le nom du pod head
$POD_HEAD = kubectl get pods -l ray.io/node-type=head -o name

# Copier et exécuter (si pas déjà dans l'image Docker)
# kubectl cp fraud_brfc.py ${POD_HEAD}:/home/ray/
kubectl exec -it ${POD_HEAD} -- python /home/ray/fraud_detection.py

# nouveau code
kubectl cp detection_object2.py [POD_HEAD]:/home/ray/detection_object2.py
kubectl exec -it [POD_HEAD] -- python /home/ray/detection_object2.py
```

### Mise à jour (Workflow de dev)

```PowerShell
# 1. Build de la nouvelle image
gcloud builds submit --tag gcr.io/[PROJECT_ID]/ray-training:latest .

# 2. Redémarrer le cluster Ray pour tirer la nouvelle image
kubectl delete raycluster --all
kubectl apply -f ray-cluster.yaml
```

## 8. Gestion des coûts (Shutdown / Wake‑up)
### Mettre en sommeil (Économies)


```PowerShell
# 1. Supprimer Ray
kubectl delete raycluster --all

# 2. Stopper MLflow et SQL
kubectl scale deployment mlflow-test --replicas=0
gcloud sql instances patch mlflow-postgres --activation-policy=NEVER

# 3. Réduire GKE à zéro
gcloud container clusters resize ray-gpu-cluster --num-nodes 0 --zone europe-west1-b
gcloud container clusters resize ray-gpu-cluster `
  --node-pool gpu-pool `
  --num-nodes 0 `
  --zone europe-west1-b

# ou les détruire
gcloud container node-pools delete gpu-pool `
  --cluster ray-gpu-cluster `
  --zone europe-west1-b
```

### Rallumer
```PowerShell
gcloud container clusters resize ray-gpu-cluster --num-nodes 3 --zone europe-west1-b
gcloud sql instances patch mlflow-postgres --activation-policy=ALWAYS
kubectl scale deployment mlflow-test --replicas=1
kubectl apply -f ray-cluster.yaml
```

### Debug secrets
```PowerShell
kubectl get externalsecret mlflow-db-password-sync

# si ce n'est pas TRUE rétablir les permissions IAM
$PROJECT_ID="ray-distributed-ml"

# 1. Donner le droit au compte GCP de lire les secrets
gcloud projects add-iam-policy-binding $PROJECT_ID `
    --member="serviceAccount:eso-gsa@${PROJECT_ID}.iam.gserviceaccount.com" `
    --role="roles/secretmanager.secretAccessor"

# 2. Autoriser le compte Kubernetes (eso-ksa) à utiliser ce compte GCP
gcloud iam service-accounts add-iam-policy-binding eso-gsa@${PROJECT_ID}.iam.gserviceaccount.com `
    --role roles/iam.workloadIdentityUser `
    --member "serviceAccount:${PROJECT_ID}.svc.id.goog[default/eso-ksa]"

# 3. Mettre à jour l'étiquette sur Kubernetes
kubectl annotate serviceaccount eso-ksa iam.gke.io/gcp-service-account=eso-gsa@${PROJECT_ID}.iam.gserviceaccount.com --overwrite

# focer la synchronisation
kubectl delete externalsecret mlflow-db-password-sync
kubectl apply -f eso-mlflow-config.yaml

# vérif 
kubectl get externalsecret mlflow-db-password-sync
```
