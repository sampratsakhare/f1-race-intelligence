from unittest.mock import Mock

import pandas as pd
import pytest

from dashboard.data_access import (
    WarehouseConfig,
    dataframe_records,
    load_driver_performance,
    load_live_sessions,
    load_live_snapshot,
    load_pit_analysis,
    load_race_catalog,
    load_table_freshness,
)


def _client_with_frame(frame: pd.DataFrame) -> Mock:
    job = Mock()
    job.to_dataframe.return_value = frame
    client = Mock()
    client.query.return_value = job
    return client


def test_warehouse_config_validates_identifiers() -> None:
    config = WarehouseConfig("my-project", "f1_raw")
    assert config.prefix == "my-project.f1_raw"

    with pytest.raises(ValueError, match="invalid"):
        WarehouseConfig("project", "bad.dataset")


@pytest.mark.parametrize(
    "reader",
    [load_race_catalog, load_table_freshness, load_live_sessions],
)
def test_unparameterized_dashboard_readers_return_dataframe(reader) -> None:
    expected = pd.DataFrame([{"value": 1}])
    client = _client_with_frame(expected)

    actual = reader(client, WarehouseConfig("my-project", "f1_raw"))

    pd.testing.assert_frame_equal(actual, expected)
    query = client.query.call_args.args[0]
    assert "`my-project.f1_raw" in query


def test_performance_reader_uses_query_parameters() -> None:
    client = _client_with_frame(pd.DataFrame())

    load_driver_performance(
        client,
        WarehouseConfig("my-project", "f1_raw"),
        year=2025,
        session_key=123,
    )

    job_config = client.query.call_args.kwargs["job_config"]
    parameter_values = {
        parameter.name: parameter.value for parameter in job_config.query_parameters
    }
    assert parameter_values == {"year": 2025, "session_key": 123}


@pytest.mark.parametrize("reader", [load_pit_analysis, load_live_snapshot])
def test_session_readers_parameterize_session_key(reader) -> None:
    client = _client_with_frame(pd.DataFrame())

    reader(
        client,
        WarehouseConfig("my-project", "f1_raw"),
        session_key=456,
    )

    job_config = client.query.call_args.kwargs["job_config"]
    assert job_config.query_parameters[0].value == 456


def test_dataframe_records_replaces_missing_values() -> None:
    records = dataframe_records(pd.DataFrame([{"value": 1.0}, {"value": None}]))
    assert records == [{"value": 1.0}, {"value": None}]
