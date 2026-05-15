import os
import tempfile

import ray
from ray import train
from ray.train import ScalingConfig, RunConfig
import ray.train.torch

import torch
import torch.nn as nn
from torch.optim import Adam
from torch.utils.data import DataLoader
from torchvision.models import resnet18
from torchvision.datasets import CIFAR10
from torchvision.transforms import ToTensor, Normalize, Compose

ARTIFACT_ROOT = os.environ.get("ARTIFACT_ROOT")

def train_func(config):
    # ----- Modèle -----
    model = resnet18(num_classes=10)  # CIFAR-10 = 3 canaux RGB -> pas de modif de conv1
    # Préparation DDP/AMP par Ray
    model = ray.train.torch.prepare_model(model)

    criterion = nn.CrossEntropyLoss()
    optimizer = Adam(model.parameters(), lr=config.get("lr", 1e-3))

    # ----- Données -----
    # Stats CIFAR-10 (RGB)
    transform = Compose(
        [
            ToTensor(),
            Normalize(mean=(0.4914, 0.4822, 0.4465), std=(0.2470, 0.2435, 0.2616)),
        ]
    )

    data_dir = os.path.join(tempfile.gettempdir(), "data")
    train_data = CIFAR10(root=data_dir, train=True, download=True, transform=transform)
    train_loader = DataLoader(
        train_data,
        batch_size=config.get("batch_size", 128),
        shuffle=True,
        num_workers=2,
    )
    train_loader = ray.train.torch.prepare_data_loader(train_loader)

    # ----- Entraînement -----
    epochs = config.get("epochs", 2)
    for epoch in range(epochs):
        if train.get_context().get_world_size() > 1:
            # DistributedSampler géré par prepare_data_loader
            try:
                train_loader.sampler.set_epoch(epoch)
            except AttributeError:
                pass

        running_loss = 0.0
        for images, labels in train_loader:
            outputs = model(images)
            loss = criterion(outputs, labels)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            running_loss += loss.item()

        avg_loss = running_loss / max(1, len(train_loader))
        train.report({"epoch": epoch, "loss": avg_loss})

    # (Optionnel) Checkpoint final (sur rank 0)
    if train.get_context().get_world_rank() == 0:
        with tempfile.TemporaryDirectory() as tmp_ckpt:
            torch.save(model.state_dict(), os.path.join(tmp_ckpt, "model.pt"))
            train.report(
                {"final_loss": avg_loss},
                checkpoint=train.Checkpoint.from_directory(tmp_ckpt),
            )


# ----- Configuration de l'entraînement distribué -----
scaling_config = ray.train.ScalingConfig(num_workers=1, use_gpu=False)


trainer = ray.train.torch.TorchTrainer(
    train_loop_per_worker=train_func,
    scaling_config=scaling_config,
    run_config=RunConfig(
        name="cifar10-resnet18", storage_path=ARTIFACT_ROOT
    ),
)

result = trainer.fit()
print(result)
