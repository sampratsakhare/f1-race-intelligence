from scripts.build_demo_snapshot import build_performance_rows


def test_performance_joins_qualifying_grid_to_race_by_meeting() -> None:
    sessions = [
        {
            "session_key": 20,
            "meeting_key": 10,
            "circuit_short_name": "Test Circuit",
            "country_name": "Testland",
            "year": 2025,
            "date_start": "2025-01-02T12:00:00Z",
        }
    ]
    drivers = [
        {
            "session_key": 20,
            "driver_number": 44,
            "full_name": "Test Driver",
            "team_name": "Test Team",
        }
    ]
    grids = [
        {
            "session_key": 19,
            "meeting_key": 10,
            "driver_number": 44,
            "position": 3,
        }
    ]
    results = [
        {
            "session_key": 20,
            "driver_number": 44,
            "position": 1,
            "number_of_laps": 50,
        }
    ]

    performance = build_performance_rows(
        sessions,
        drivers,
        grids,
        results,
    )

    assert performance.iloc[0]["starting_position"] == 3
    assert performance.iloc[0]["finishing_position"] == 1
    assert performance.iloc[0]["positions_gained"] == 2
