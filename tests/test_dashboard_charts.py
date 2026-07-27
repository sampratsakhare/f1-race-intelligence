import pandas as pd

from dashboard.app import (
    _grid_finish_chart,
    _pace_consistency_chart,
    _pit_duration_chart,
    _season_driver_chart,
)


def _race_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "session_key": 1,
                "full_name": "Driver A",
                "team_name": "Team Red",
                "starting_position": 2,
                "finishing_position": 1,
                "positions_gained": 1,
                "avg_lap_seconds": 90.0,
                "lap_consistency_stddev": 0.4,
                "clean_laps_analyzed": 40,
                "did_not_finish": False,
            },
            {
                "session_key": 1,
                "full_name": "Driver B",
                "team_name": "Team Blue",
                "starting_position": 1,
                "finishing_position": 2,
                "positions_gained": -1,
                "avg_lap_seconds": 90.5,
                "lap_consistency_stddev": 0.7,
                "clean_laps_analyzed": 38,
                "did_not_finish": False,
            },
        ]
    )


def test_race_charts_serialize_with_expected_encodings() -> None:
    race = _race_frame()

    grid_spec = _grid_finish_chart(race).to_dict()
    pace_spec = _pace_consistency_chart(race).to_dict()

    assert len(grid_spec["layer"]) == 3
    assert grid_spec["layer"][0]["encoding"]["x"]["field"] == "starting_position"
    assert pace_spec["encoding"]["x"]["field"] == "pace_delta_pct"
    assert pace_spec["encoding"]["y"]["field"] == "lap_consistency_stddev"


def test_season_chart_uses_results_not_cross_circuit_lap_time() -> None:
    race = _race_frame()
    second_race = race.assign(
        session_key=2,
        finishing_position=[3, 1],
        positions_gained=[-1, 0],
    )

    spec = _season_driver_chart(pd.concat([race, second_race])).to_dict()

    assert spec["encoding"]["x"]["field"] == "average_finish"
    assert spec["encoding"]["y"]["field"] == "average_positions_gained"


def test_pit_chart_labels_position_change_as_observed() -> None:
    pits = pd.DataFrame(
        [
            {
                "full_name": "Driver A",
                "team_name": "Team Red",
                "lap_number": 20,
                "lane_duration_seconds": 22.5,
                "stop_duration_seconds": 2.3,
                "tire_compound_fitted": "HARD",
                "positions_changed_around_stop": -1,
            }
        ]
    )

    spec = _pit_duration_chart(pits).to_dict()
    tooltip_titles = {item.get("title") for item in spec["encoding"]["tooltip"]}

    assert "Observed position change" in tooltip_titles
