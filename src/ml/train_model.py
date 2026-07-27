"""Train and validate a leakage-safe pre-race finishing-order model.

The model only uses the source starting grid plus historical features that
were available before each race. Evaluation holds out complete races in
chronological order and compares the candidate against the starting grid.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery
from lightgbm import LGBMRegressor
from sklearn.compose import ColumnTransformer
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TARGET_COLUMN = "finishing_position"
CATEGORICAL_FEATURES = [
    "full_name",
    "team_name",
    "circuit_short_name",
]
NUMERIC_FEATURES = [
    "starting_position",
    "driver_avg_finish_last_5",
    "driver_avg_positions_gained_last_5",
    "driver_dnf_rate_last_5",
    "driver_circuit_avg_finish_prior",
    "driver_races_prior",
    "team_avg_finish_last_5",
    "team_dnf_rate_last_5",
    "team_races_prior",
]
FEATURE_COLUMNS = CATEGORICAL_FEATURES + NUMERIC_FEATURES
MIN_PROMOTION_TEST_RACES = 5
BOOTSTRAP_SAMPLES = 10_000
REQUIRED_INPUT_COLUMNS = {
    "session_key",
    "session_start_ts",
    "full_name",
    "team_name",
    "circuit_short_name",
    "starting_position",
    "finishing_position",
    "positions_gained",
    "did_not_finish",
    "did_not_start",
    "disqualified",
}


def load_dataset(
    csv_path: Path | None,
    table: str | None,
) -> pd.DataFrame:
    if csv_path is not None:
        return pd.read_csv(csv_path)

    project_id = os.environ["GCP_PROJECT_ID"]
    dataset = os.environ.get("BQ_DATASET", "f1_raw")
    table_ref = table or f"{project_id}.{dataset}.driver_race_performance"
    client = bigquery.Client(project=project_id)
    query = f"""
        SELECT *
        FROM `{table_ref}`
        ORDER BY session_start_ts, session_key, driver_number
    """
    return client.query(query).to_dataframe()


def _rolling_prior(
    data: pd.DataFrame,
    group_columns: list[str],
    value_column: str,
    window: int = 5,
) -> pd.Series:
    return data.groupby(group_columns, sort=False)[value_column].transform(
        lambda values: values.shift(1).rolling(window, min_periods=1).mean()
    )


def build_pre_race_features(raw: pd.DataFrame) -> pd.DataFrame:
    """Create features using only results from races before each row."""
    missing = REQUIRED_INPUT_COLUMNS - set(raw.columns)
    if missing:
        raise ValueError(f"Input is missing required columns: {sorted(missing)}")

    data = raw.copy()
    data["session_start_ts"] = pd.to_datetime(
        data["session_start_ts"],
        utc=True,
        errors="raise",
    )
    data["did_not_finish"] = data["did_not_finish"].fillna(False).astype(bool)
    data["did_not_start"] = data["did_not_start"].fillna(False).astype(bool)
    data["disqualified"] = data["disqualified"].fillna(False).astype(bool)
    data["non_finish"] = (
        data["did_not_finish"] | data["did_not_start"] | data["disqualified"]
    ).astype(float)
    data = data.sort_values(["session_start_ts", "session_key", "full_name"]).reset_index(drop=True)

    data["driver_avg_finish_last_5"] = _rolling_prior(
        data,
        ["full_name"],
        "finishing_position",
    )
    data["driver_avg_positions_gained_last_5"] = _rolling_prior(
        data,
        ["full_name"],
        "positions_gained",
    )
    data["driver_dnf_rate_last_5"] = _rolling_prior(
        data,
        ["full_name"],
        "non_finish",
    )
    data["driver_circuit_avg_finish_prior"] = _rolling_prior(
        data,
        ["full_name", "circuit_short_name"],
        "finishing_position",
        window=100,
    )
    data["driver_races_prior"] = data.groupby(
        "full_name",
        sort=False,
    ).cumcount()

    team_race = (
        data.groupby(
            ["team_name", "session_key", "session_start_ts"],
            as_index=False,
        )
        .agg(
            team_race_avg_finish=("finishing_position", "mean"),
            team_race_dnf_rate=("non_finish", "mean"),
        )
        .sort_values(["team_name", "session_start_ts", "session_key"])
    )
    team_race["team_avg_finish_last_5"] = _rolling_prior(
        team_race,
        ["team_name"],
        "team_race_avg_finish",
    )
    team_race["team_dnf_rate_last_5"] = _rolling_prior(
        team_race,
        ["team_name"],
        "team_race_dnf_rate",
    )
    team_race["team_races_prior"] = team_race.groupby(
        "team_name",
        sort=False,
    ).cumcount()

    return data.merge(
        team_race[
            [
                "team_name",
                "session_key",
                "team_avg_finish_last_5",
                "team_dnf_rate_last_5",
                "team_races_prior",
            ]
        ],
        on=["team_name", "session_key"],
        how="left",
        validate="many_to_one",
    )


def chronological_race_split(
    data: pd.DataFrame,
    test_fraction: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between zero and one")

    sessions = (
        data[["session_key", "session_start_ts"]]
        .drop_duplicates()
        .sort_values(["session_start_ts", "session_key"])
    )
    if len(sessions) < 5:
        raise ValueError("At least five races are required for chronological validation")

    test_races = max(1, math.ceil(len(sessions) * test_fraction))
    test_session_keys = set(sessions.tail(test_races)["session_key"])
    train = data[~data["session_key"].isin(test_session_keys)].copy()
    test = data[data["session_key"].isin(test_session_keys)].copy()
    return train, test


def build_pipeline() -> Pipeline:
    preprocessing = ColumnTransformer(
        transformers=[
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                CATEGORICAL_FEATURES,
            ),
            ("numeric", "passthrough", NUMERIC_FEATURES),
        ],
        remainder="drop",
    )
    model = LGBMRegressor(
        n_estimators=300,
        learning_rate=0.03,
        max_depth=4,
        num_leaves=15,
        min_child_samples=20,
        reg_alpha=0.1,
        reg_lambda=0.1,
        random_state=42,
        verbosity=-1,
    )
    return Pipeline(
        [
            ("preprocessing", preprocessing),
            ("model", model),
        ]
    )


def rank_within_race(
    frame: pd.DataFrame,
    raw_predictions: np.ndarray,
) -> pd.Series:
    prediction_frame = frame[["session_key"]].copy()
    prediction_frame["raw_prediction"] = raw_predictions
    return prediction_frame.groupby("session_key")["raw_prediction"].rank(
        method="first",
        ascending=True,
    )


def _top_three_accuracy(
    actual: pd.Series,
    predicted_rank: pd.Series,
) -> float:
    actual_top_three = actual <= 3
    predicted_top_three = predicted_rank <= 3
    denominator = int(actual_top_three.sum())
    if denominator == 0:
        return float("nan")
    return float((actual_top_three & predicted_top_three).sum() / denominator)


def _mean_race_rank_correlation(
    frame: pd.DataFrame,
    predicted_rank: pd.Series,
) -> float:
    values = frame[["session_key", TARGET_COLUMN]].copy()
    values["predicted_rank"] = predicted_rank.to_numpy()
    correlations = values.groupby("session_key").apply(
        lambda race: race[TARGET_COLUMN].corr(
            race["predicted_rank"],
            method="spearman",
        ),
        include_groups=False,
    )
    return float(correlations.mean())


def _race_level_improvement(
    test: pd.DataFrame,
    predicted_rank: pd.Series,
) -> pd.Series:
    values = test[["session_key", TARGET_COLUMN, "starting_position"]].copy()
    values["predicted_rank"] = predicted_rank.to_numpy()
    values["model_absolute_error"] = (values[TARGET_COLUMN] - values["predicted_rank"]).abs()
    values["baseline_absolute_error"] = (values[TARGET_COLUMN] - values["starting_position"]).abs()
    race_metrics = values.groupby("session_key").agg(
        model_mae=("model_absolute_error", "mean"),
        baseline_mae=("baseline_absolute_error", "mean"),
    )
    return race_metrics["baseline_mae"] - race_metrics["model_mae"]


def _bootstrap_mean_interval(
    values: pd.Series,
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = 42,
) -> tuple[float, float]:
    observed = values.dropna().to_numpy(dtype=float)
    if len(observed) == 0:
        return float("nan"), float("nan")
    generator = np.random.default_rng(seed)
    resampled_means = generator.choice(
        observed,
        size=(samples, len(observed)),
        replace=True,
    ).mean(axis=1)
    low, high = np.quantile(resampled_means, [0.025, 0.975])
    return float(low), float(high)


def evaluate(
    pipeline: Pipeline,
    test: pd.DataFrame,
) -> dict[str, float]:
    raw_predictions = pipeline.predict(test[FEATURE_COLUMNS])
    predicted_rank = rank_within_race(test, raw_predictions)
    actual = test[TARGET_COLUMN].astype(float)
    baseline = test["starting_position"].astype(float)
    race_improvement = _race_level_improvement(test, predicted_rank)
    improvement_low, improvement_high = _bootstrap_mean_interval(race_improvement)

    return {
        "model_mae": float(mean_absolute_error(actual, predicted_rank)),
        "grid_baseline_mae": float(mean_absolute_error(actual, baseline)),
        "model_top3_accuracy": _top_three_accuracy(actual, predicted_rank),
        "grid_top3_accuracy": _top_three_accuracy(actual, baseline),
        "model_mean_spearman": _mean_race_rank_correlation(
            test,
            predicted_rank,
        ),
        "grid_mean_spearman": _mean_race_rank_correlation(test, baseline),
        "model_race_win_rate": float((race_improvement > 0).mean()),
        "mean_race_mae_improvement": float(race_improvement.mean()),
        "race_mae_improvement_ci95_low": improvement_low,
        "race_mae_improvement_ci95_high": improvement_high,
    }


def train_and_evaluate(
    raw: pd.DataFrame,
    test_fraction: float = 0.2,
) -> tuple[Pipeline, dict[str, Any], pd.DataFrame, pd.DataFrame]:
    features = build_pre_race_features(raw)
    eligible = features.dropna(subset=[TARGET_COLUMN, "starting_position"]).copy()
    train, test = chronological_race_split(eligible, test_fraction)

    pipeline = build_pipeline()
    pipeline.fit(train[FEATURE_COLUMNS], train[TARGET_COLUMN])
    metrics: dict[str, Any] = evaluate(pipeline, test)
    metrics.update(
        {
            "input_rows": len(features),
            "eligible_classified_rows": len(eligible),
            "excluded_unclassified_rows": len(features) - len(eligible),
            "classified_target_coverage_pct": len(eligible) / len(features),
            "training_rows": len(train),
            "test_rows": len(test),
            "training_races": int(train["session_key"].nunique()),
            "test_races": int(test["session_key"].nunique()),
            "test_start": test["session_start_ts"].min().isoformat(),
            "test_end": test["session_start_ts"].max().isoformat(),
        }
    )
    metrics["mae_improvement"] = metrics["grid_baseline_mae"] - metrics["model_mae"]
    metrics["mae_improvement_pct"] = metrics["mae_improvement"] / metrics["grid_baseline_mae"]
    metrics["promoted"] = (
        metrics["test_races"] >= MIN_PROMOTION_TEST_RACES
        and metrics["model_mae"] < metrics["grid_baseline_mae"]
        and metrics["race_mae_improvement_ci95_low"] > 0
    )
    return pipeline, metrics, train, test


def save_outputs(
    pipeline: Pipeline,
    metrics: dict[str, Any],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = output_dir / "finishing_position_candidate.joblib"
    metrics_path = output_dir / "finishing_position_metrics.json"
    joblib.dump(
        {
            "pipeline": pipeline,
            "feature_columns": FEATURE_COLUMNS,
            "metrics": metrics,
            "trained_at": datetime.now(UTC).isoformat(),
        },
        candidate_path,
    )
    metrics_path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    if metrics["promoted"]:
        champion_path = output_dir / "finishing_position_model.joblib"
        joblib.dump(
            {
                "pipeline": pipeline,
                "feature_columns": FEATURE_COLUMNS,
                "metrics": metrics,
                "trained_at": datetime.now(UTC).isoformat(),
            },
            champion_path,
        )
        logger.info("Candidate beat the grid baseline and was promoted to %s", champion_path)
    else:
        logger.warning(
            "Candidate was not promoted: model MAE %.3f vs grid baseline %.3f",
            metrics["model_mae"],
            metrics["grid_baseline_mae"],
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train a leakage-safe pre-race finishing-order model"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, help="Optional mart CSV")
    source.add_argument(
        "--bigquery-table",
        help="Optional project.dataset.table; defaults to environment settings",
    )
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--output-dir", type=Path, default=Path("models"))
    args = parser.parse_args()

    raw = load_dataset(args.input, args.bigquery_table)
    pipeline, metrics, _, _ = train_and_evaluate(raw, args.test_fraction)
    save_outputs(pipeline, metrics, args.output_dir)
    logger.info("Evaluation:\n%s", json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
