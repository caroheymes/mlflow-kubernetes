import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import GridSearchCV
from ray.util.joblib import register_ray
import joblib
import ray
import mlflow
from mlflow.models import infer_signature  # ⬅️ NEW
import os

mlflow.set_tracking_uri("http://mlflow-test-service.default.svc.cluster.local:5000")
mlflow.sklearn.autolog()
mlflow.set_experiment("ray")
# ray.init(address="auto")

credit_card_data = pd.read_csv(
    "https://lead-program-assets.s3.eu-west-3.amazonaws.com/M01-Distributed_machine_learning/datasets/creditcard.csv"
)

X_train = credit_card_data.loc[:, credit_card_data.columns != "Class"]
y_train = credit_card_data.loc[
    :, credit_card_data.columns == "Class"
]  # DataFrame à 1 col OK


model = Pipeline(
    steps=[
        ("standard_scaler", StandardScaler()),
        ("classifier", RandomForestClassifier()),
    ],
    verbose=True,
)

param_space = {
    "classifier__n_estimators": [10],
    "classifier__max_depth": [3],
    "classifier__min_samples_split": [2],
}

grid_search = GridSearchCV(
    model, param_grid=param_space, n_jobs=-1, cv=5, verbose=2, refit=True
)

register_ray()
with joblib.parallel_backend("ray"):
    with mlflow.start_run() as run:
        grid_search.fit(
            X_train, y_train.values.ravel()
        )  # ravel pour sklearn, au cas où
        print(grid_search.score(X_train, y_train))

        # ⬇️ NEW: signature + input_example
        input_example = X_train.iloc[:5]  # petit batch d’exemple
        # Utilise la prédiction du meilleur modèle pour inférer la signature I/O
        y_pred_example = grid_search.best_estimator_.predict(input_example)
        signature = infer_signature(input_example, y_pred_example)

        # Log explicite du modèle avec signature & input_example
        mlflow.sklearn.log_model(
            sk_model=grid_search.best_estimator_,
            artifact_path="model",
            signature=signature,
            input_example=input_example,
        )
