"""
Trains a gradient-boosted model to predict a driver's finishing position
using features from the driver_race_performance mart.

IMPORTANT LIMITATION (documented honestly rather than hidden):
Several features here (avg_lap_seconds, pit_stop_count, total_pit_time_seconds,
etc.) are computed FROM the race whose outcome we're predicting. That makes
this a "what factors correlate with how a race ended" model, not a genuine
pre-race or in-race prediction system -- a real predictive product would need
a defined horizon (e.g. "using only data through lap 10, predict final
position") built from cumulative, point-in-time features. That's flagged as
future work rather than solved here, since it requires rebuilding the mart
with lap-indexed cumulative features.

What this script DOES do properly:
- Compares the model against a naive baseline (predict finishing position =
  starting position) so the result is honestly contextualized rather than
  presented as an unqualified number.
- Splits train/test BY RACE (session_key), not by row, so no race's data
  leaks across the train/test boundary.

Run:
    python -m src.ml.train_model --input data/marts/driver_race_performance.csv

Expects a CSV export of the driver_race_performance BigQuery mart
(bq query --format=csv or the BigQuery console "Save results" -> CSV).
"""

from __future__ import annotations

import argparse
import logging
import os

import joblib
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupShuffleSplit

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
GROUP_COLUMN = "session_key"  # group by race so a race never spans train+test
MODEL_OUTPUT_PATH = "models/finishing_position_model.joblib"


def load_and_prepare(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    required = [TARGET_COLUMN, GROUP_COLUMN] + FEATURE_COLUMNS
    df = df.dropna(subset=required)
    return df


def split_by_race(df: pd.DataFrame):
    """Group-based split: every row from a given race stays entirely in
    either train or test, preventing the model from ever seeing part of a
    race it's being tested on."""
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
    train_idx, test_idx = next(splitter.split(df, groups=df[GROUP_COLUMN]))
    return df.iloc[train_idx], df.iloc[test_idx]


def train(train_df: pd.DataFrame, test_df: pd.DataFrame) -> LGBMRegressor:
    X_train, y_train = train_df[FEATURE_COLUMNS], train_df[TARGET_COLUMN]
    X_test, y_test = test_df[FEATURE_COLUMNS], test_df[TARGET_COLUMN]

    model = LGBMRegressor(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        random_state=42,
        verbose=-1,  # quiet the "no further splits" chatter, it's harmless but noisy
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    model_mae = mean_absolute_error(y_test, preds)

    # Naive baseline: "you'll finish where you started." Any model claiming
    # value has to beat this, or the model isn't actually adding anything.
    baseline_preds = X_test["starting_position"]
    baseline_mae = mean_absolute_error(y_test, baseline_preds)

    logger.info("Test set: %d races, %d rows", test_df[GROUP_COLUMN].nunique(), len(test_df))
    logger.info("Model MAE:            %.3f positions", model_mae)
    logger.info("Baseline MAE (grid=finish): %.3f positions", baseline_mae)

    if model_mae < baseline_mae:
        logger.info("Model beats the naive baseline by %.3f positions.", baseline_mae - model_mae)
    else:
        logger.warning(
            "Model does NOT beat the naive baseline (worse by %.3f positions). "
            "Report this honestly -- see the module docstring for why (feature leakage).",
            model_mae - baseline_mae,
        )

    feature_importance = pd.Series(
        model.feature_importances_, index=FEATURE_COLUMNS
    ).sort_values(ascending=False)
    logger.info("Feature importance:\n%s", feature_importance.to_string())

    return model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train finishing-position model")
    parser.add_argument("--input", required=True, help="Path to driver_race_performance CSV export")
    args = parser.parse_args()

    df = load_and_prepare(args.input)
    logger.info("Loaded %d rows across %d races, %d features",
                len(df), df[GROUP_COLUMN].nunique(), len(FEATURE_COLUMNS))

    train_df, test_df = split_by_race(df)
    model = train(train_df, test_df)

    os.makedirs("models", exist_ok=True)
    joblib.dump(model, MODEL_OUTPUT_PATH)
    logger.info("Saved model to %s", MODEL_OUTPUT_PATH)


if __name__ == "__main__":
    main()
