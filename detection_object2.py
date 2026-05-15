import os
import tempfile

import torch
from torch.nn import CrossEntropyLoss
from torch.optim import Adam
from torch.utils.data import DataLoader
from torchvision.models import resnet18
from torchvision.datasets import CIFAR10
from torchvision.transforms import ToTensor, Normalize, Compose

import ray.train.torch
from ray.train import ScalingConfig, RunConfig, Checkpoint
import mlflow  # [NOUVEAU] Import de base de MLflow

def train_func():
    # --- [NOUVEAU] Initialisation de MLflow (Seulement sur le worker 0) ---
    is_chief = ray.train.get_context().get_world_rank() == 0
    if is_chief:
        mlflow.set_tracking_uri("http://mlflow-test-service.default.svc.cluster.local:5000")
        mlflow.set_experiment("computer vision")
        mlflow.start_run(run_name="autonomous_car_run")

    # Model, Loss, Optimizer
    model = resnet18(num_classes=10)
    
    # [CORRECTION BUGS PYTORCH] CIFAR10 a 3 canaux (RGB), pas 1 !
    model.conv1 = torch.nn.Conv2d(
        3, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False
    )
    
    # [1] Prepare model.
    model = ray.train.torch.prepare_model(model)
    criterion = CrossEntropyLoss()
    optimizer = Adam(model.parameters(), lr=0.001)

    # Data
    transform = Compose([ToTensor(), Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
    data_dir = os.path.join(tempfile.gettempdir(), "data")
    train_data = CIFAR10(root=data_dir, train=True, download=True, transform=transform)
    train_loader = DataLoader(train_data, batch_size=32, shuffle=True)
    
    # [2] Prepare dataloader.
    train_loader = ray.train.torch.prepare_data_loader(train_loader)

    # Training
    for epoch in range(10):
        if ray.train.get_context().get_world_size() > 1:
            train_loader.sampler.set_epoch(epoch)

        for images, labels in train_loader:
            outputs = model(images)
            loss = criterion(outputs, labels)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        # [3] Report metrics and checkpoint
        metrics = {"loss": loss.item(), "epoch": epoch}
        
        # [NOUVEAU] Envoi des métriques à MLflow (Worker 0 uniquement)
        if is_chief:
            mlflow.log_metrics(metrics, step=epoch)
        
        with tempfile.TemporaryDirectory() as temp_checkpoint_dir:
            torch.save(
                model.module.state_dict(),
                os.path.join(temp_checkpoint_dir, "model.pt")
            )
            ray.train.report(
                metrics,
                checkpoint=Checkpoint.from_directory(temp_checkpoint_dir),
            )
            
    # [NOUVEAU] Clôture du run MLflow proprement
    if is_chief:
        mlflow.end_run()

# [4] Configure scaling and resource requirements.
# Pense à mettre use_gpu=False si tes GPU n'ont pas pu être créés par GCP !
scaling_config = ScalingConfig(num_workers=2, use_gpu=False)

# [5] Launch distributed training job.
trainer = ray.train.torch.TorchTrainer(
    train_func,
    scaling_config=scaling_config,
    run_config=RunConfig(
        storage_path="gs://mlflow-artifacts-002/", 
        name="autonomous_car"
        # Plus de Callback ici !
    )
) 

result = trainer.fit()