"""
Trains a gradient-boosted model to predict a driver's finishing position
using features from the driver_race_performance mart.

This is deliberately simple: a defensible tabular model beats an
overengineered deep learning model nobody asked for, for this use case.

Run:
    python -m src.ml.train_model --input data/marts/driver_race_performance.csv

Expects a CSV export of the driver_race_performance BigQuery mart
(bq query --format=csv or the BigQuery console "Save results" -> CSV).
"""

from __future__ import annotations

import argparse
import logging

import joblib
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FEATURE_COLUMNS = [
    "starting_position",
    "avg_lap_seconds",
    "best_lap_seconds",
    "lap_consistency_stddev",
    "pit_stop_count",
    "avg_pit_duration_seconds",
    "total_pit_time_seconds",
]
TARGET_COLUMN = "finishing_position"
MODEL_OUTPUT_PATH = "models/finishing_position_model.joblib"


def load_and_prepare(csv_path: str) -> tuple[pd.DataFrame, pd.Series]:
    df = pd.read_csv(csv_path)

    # Drop rows missing the target or key features -- DNFs and incomplete
    # sessions aren't useful for this first-pass model.
    df = df.dropna(subset=[TARGET_COLUMN] + FEATURE_COLUMNS)

    X = df[FEATURE_COLUMNS]
    y = df[TARGET_COLUMN]
    return X, y


def train(X: pd.DataFrame, y: pd.Series) -> LGBMRegressor:
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    model = LGBMRegressor(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        random_state=42,
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    logger.info("Test MAE: %.2f positions", mae)

    feature_importance = pd.Series(
        model.feature_importances_, index=FEATURE_COLUMNS
    ).sort_values(ascending=False)
    logger.info("Feature importance:\n%s", feature_importance.to_string())

    return model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train finishing-position model")
    parser.add_argument("--input", required=True, help="Path to driver_race_performance CSV export")
    args = parser.parse_args()

    X, y = load_and_prepare(args.input)
    logger.info("Training on %d rows, %d features", len(X), len(FEATURE_COLUMNS))

    model = train(X, y)

    import os
    os.makedirs("models", exist_ok=True)
    joblib.dump(model, MODEL_OUTPUT_PATH)
    logger.info("Saved model to %s", MODEL_OUTPUT_PATH)


if __name__ == "__main__":
    main()
