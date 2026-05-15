# On part sur l'image avec Python 3.12 NVIDIA
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