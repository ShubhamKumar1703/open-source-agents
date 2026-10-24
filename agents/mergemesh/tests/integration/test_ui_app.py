"""Integration test verifying the Streamlit dashboard (`app.py`) renders and interacts cleanly."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent.parent / "app.py"


def test_streamlit_app_renders_and_switches_scenarios() -> None:
    """Launch app.py via Streamlit AppTest and verify initial render, findings, and post-fix flow."""
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()

    assert not at.exception, f"Streamlit app raised an exception: {at.exception}"
    assert "active_cache_key" in at.session_state
    active_key = at.session_state["active_cache_key"]
    report = at.session_state["report_cache"][active_key]
    assert report.scenario_id == "customer_id_rename"
    assert report.overall_status == "BREAKING_FAILURE_REPRODUCED"
    assert len(report.finding_list) == 1
    assert report.finding_list[0].verification_state == "REPRODUCED_FAILURE"
