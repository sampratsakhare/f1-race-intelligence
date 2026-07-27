from streamlit.testing.v1 import AppTest


def test_bundled_demo_renders_full_dashboard(monkeypatch) -> None:
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("DASHBOARD_MODE", raising=False)

    app = AppTest.from_file("dashboard/app.py").run(timeout=30)

    assert len(app.exception) == 0
    assert len(app.error) == 0
    assert [tab.label for tab in app.tabs] == [
        "Race intelligence",
        "Live / replay",
        "Methodology",
    ]
    assert len(app.metric) >= 12
    assert len(app.dataframe) >= 3
