from pathlib import Path

import pandas as pd

from src.ml.train_model import (
    NUMERIC_FEATURES,
    build_pre_race_features,
    chronological_race_split,
    save_outputs,
    train_and_evaluate,
)


def sample_races(race_count: int = 8, driver_count: int = 10) -> pd.DataFrame:
    rows = []
    for race_index in range(race_count):
        session_key = 1_000 + race_index
        for driver_index in range(driver_count):
            grid = driver_index + 1
            finish = ((driver_index + race_index) % driver_count) + 1
            rows.append(
                {
                    "session_key": session_key,
                    "session_start_ts": f"2025-{race_index + 1:02d}-01T12:00:00Z",
                    "full_name": f"Driver {driver_index}",
                    "team_name": f"Team {driver_index // 2}",
                    "circuit_short_name": f"Circuit {race_index % 3}",
                    "starting_position": grid,
                    "finishing_position": finish,
                    "positions_gained": grid - finish,
                    "did_not_finish": False,
                    "did_not_start": False,
                    "disqualified": False,
                }
            )
    return pd.DataFrame(rows)


def test_current_race_outcome_does_not_change_its_pre_race_features() -> None:
    raw = sample_races()
    original = build_pre_race_features(raw)

    changed = raw.copy()
    last_session = changed["session_key"].max()
    changed.loc[
        changed["session_key"] == last_session,
        "finishing_position",
    ] = list(reversed(range(1, 11)))
    rebuilt = build_pre_race_features(changed)

    original_last = original[original["session_key"] == last_session]
    rebuilt_last = rebuilt[rebuilt["session_key"] == last_session]
    pd.testing.assert_frame_equal(
        original_last[NUMERIC_FEATURES].reset_index(drop=True),
        rebuilt_last[NUMERIC_FEATURES].reset_index(drop=True),
    )


def test_chronological_split_holds_out_complete_races() -> None:
    features = build_pre_race_features(sample_races())
    train, test = chronological_race_split(features, test_fraction=0.25)

    assert set(train["session_key"]).isdisjoint(set(test["session_key"]))
    assert train["session_start_ts"].max() < test["session_start_ts"].min()
    assert test["session_key"].nunique() == 2


def test_training_reports_candidate_and_baseline_metrics() -> None:
    _, metrics, train, test = train_and_evaluate(sample_races())

    assert metrics["training_races"] == train["session_key"].nunique()
    assert metrics["test_races"] == test["session_key"].nunique()
    assert metrics["model_mae"] >= 0
    assert metrics["grid_baseline_mae"] >= 0
    assert isinstance(metrics["promoted"], bool)
    assert metrics["classified_target_coverage_pct"] == 1.0
    assert metrics["excluded_unclassified_rows"] == 0
    assert 0 <= metrics["model_race_win_rate"] <= 1
    assert (
        metrics["race_mae_improvement_ci95_low"]
        <= metrics["mean_race_mae_improvement"]
        <= metrics["race_mae_improvement_ci95_high"]
    )


def test_training_reports_unclassified_target_exclusions() -> None:
    raw = sample_races()
    raw.loc[raw.index[0], "finishing_position"] = None
    raw.loc[raw.index[0], "positions_gained"] = None

    _, metrics, _, _ = train_and_evaluate(raw)

    assert metrics["input_rows"] == len(raw)
    assert metrics["eligible_classified_rows"] == len(raw) - 1
    assert metrics["excluded_unclassified_rows"] == 1


def test_underperforming_candidate_is_not_promoted(tmp_path: Path) -> None:
    metrics = {
        "promoted": False,
        "model_mae": 3.0,
        "grid_baseline_mae": 2.0,
    }
    save_outputs({}, metrics, tmp_path)  # type: ignore[arg-type]

    assert (tmp_path / "finishing_position_candidate.joblib").exists()
    assert (tmp_path / "finishing_position_metrics.json").exists()
    assert not (tmp_path / "finishing_position_model.joblib").exists()
