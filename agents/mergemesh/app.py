"""MergeMesh — Cross-Repository Compatibility Analyzer (Streamlit Entry Point)."""

from __future__ import annotations

import streamlit as st

from config.settings import check_provider_readiness, load_settings
from services.analyze_service import compute_analysis_cache_key, run_scenario_analysis
from services.report_export import save_report_artifacts
from services.scenario_loader import list_available_scenarios, load_scenario_definition
from ui.components import render_header, render_provider_readiness_sidebar
from ui.layout import (
    _compute_run_status_label,
    render_ai_explanation_tab,
    render_contract_tab,
    render_overview_tab,
    render_pr_changes_tab,
    render_test_evidence_tab,
)
from ui.styles import DASHBOARD_CSS


def main() -> None:
    st.set_page_config(
        page_title="MergeMesh — Cross-Repository Compatibility Analyzer",
        page_icon="🔗",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

    # Initialize session state
    if "selected_scenario_id" not in st.session_state:
        st.session_state["selected_scenario_id"] = "customer_id_rename"
    if "selected_ai_mode" not in st.session_state:
        try:
            default_settings = load_settings()
            st.session_state["selected_ai_mode"] = (
                default_settings.ai_mode
                if default_settings.ai_mode in {"local", "deterministic"}
                else "local"
            )
        except ValueError:
            st.session_state["selected_ai_mode"] = "local"
    if "report_cache" not in st.session_state:
        st.session_state["report_cache"] = {}
    if "active_cache_key" not in st.session_state:
        st.session_state["active_cache_key"] = None
    if "last_error" not in st.session_state:
        st.session_state["last_error"] = None
    if "post_fix_enabled" not in st.session_state:
        st.session_state["post_fix_enabled"] = False

    # Sidebar controls
    st.sidebar.markdown("## MergeMesh Controls")
    st.sidebar.caption("Local-Only Runtime (Zero API Credits Required)")

    scenarios = list_available_scenarios()
    scenario_map = {s.id: s for s in scenarios}
    scenario_ids = list(scenario_map.keys())

    current_sid = st.session_state["selected_scenario_id"]
    if current_sid not in scenario_ids and scenario_ids:
        current_sid = scenario_ids[0]

    selected_sid = st.sidebar.selectbox(
        "Select Change-Set Scenario",
        options=scenario_ids,
        index=scenario_ids.index(current_sid) if current_sid in scenario_ids else 0,
        format_func=lambda sid: scenario_map[sid].title,
    )
    if selected_sid != st.session_state["selected_scenario_id"]:
        st.session_state["selected_scenario_id"] = selected_sid
        st.session_state["post_fix_enabled"] = False

    ai_modes = ["local", "deterministic"]
    mode_labels = {
        "local": "local — Local Gemma via Ollama + ADK (Auto Fallback)",
        "deterministic": "deterministic — Pure Deterministic Engine (No LLM)",
    }
    selected_mode = st.sidebar.selectbox(
        "Execution & Explanation Mode",
        options=ai_modes,
        index=ai_modes.index(st.session_state["selected_ai_mode"])
        if st.session_state["selected_ai_mode"] in ai_modes
        else 0,
        format_func=lambda m: mode_labels.get(m, m),
    )
    st.session_state["selected_ai_mode"] = selected_mode

    settings = load_settings(override_ai_mode=selected_mode)
    readiness = check_provider_readiness(settings, timeout_sec=1.0)

    def _trigger_analysis(run_post_fix: bool = False, force_recompute: bool = False) -> None:
        st.session_state["last_error"] = None
        st.session_state["post_fix_enabled"] = run_post_fix
        try:
            cache_key = compute_analysis_cache_key(
                scenario_id=st.session_state["selected_scenario_id"],
                settings=settings,
                include_ai=(selected_mode != "deterministic"),
                run_post_fix=run_post_fix,
            )
            if force_recompute or cache_key not in st.session_state["report_cache"]:
                with st.spinner("Running local deterministic contract analysis, pytest checks, and ADK workflow..."):
                    report = run_scenario_analysis(
                        scenario_id=st.session_state["selected_scenario_id"],
                        settings=settings,
                        include_ai=(selected_mode != "deterministic"),
                        run_post_fix=run_post_fix,
                    )
                    save_report_artifacts(report)
                    st.session_state["report_cache"][cache_key] = report
            st.session_state["active_cache_key"] = cache_key
        except Exception as exc:
            st.session_state["last_error"] = str(exc)

    if st.sidebar.button("Analyze Change Set", type="primary", use_container_width=True):
        _trigger_analysis(run_post_fix=False, force_recompute=False)

    if selected_sid == "customer_id_rename":
        if st.sidebar.button("Verify Suggested Fix (Post-Fix Pass)", use_container_width=True):
            _trigger_analysis(run_post_fix=True, force_recompute=True)

    if st.sidebar.button("Reset Demo", use_container_width=True):
        st.session_state["report_cache"] = {}
        st.session_state["active_cache_key"] = None
        st.session_state["last_error"] = None
        st.session_state["post_fix_enabled"] = False
        st.session_state["selected_scenario_id"] = "customer_id_rename"
        st.rerun()

    st.sidebar.divider()
    render_provider_readiness_sidebar(readiness, settings)

    # Auto-run initial default scenario on first load so the dashboard is immediately demonstrable
    if st.session_state["active_cache_key"] is None and st.session_state["last_error"] is None:
        _trigger_analysis(run_post_fix=False, force_recompute=False)

    # If user switched scenario or AI mode, ensure active report matches selection
    expected_key = compute_analysis_cache_key(
        scenario_id=st.session_state["selected_scenario_id"],
        settings=settings,
        include_ai=(selected_mode != "deterministic"),
        run_post_fix=st.session_state["post_fix_enabled"],
    )
    if expected_key in st.session_state["report_cache"]:
        st.session_state["active_cache_key"] = expected_key
    elif st.session_state["active_cache_key"] != expected_key:
        _trigger_analysis(run_post_fix=st.session_state["post_fix_enabled"], force_recompute=False)

    active_report = st.session_state["report_cache"].get(st.session_state["active_cache_key"])
    run_status = _compute_run_status_label(active_report, st.session_state["last_error"])

    render_header(settings, run_status)

    if st.session_state["last_error"]:
        st.error(f"**Analysis Error:** {st.session_state['last_error']}")
        return

    if active_report is None:
        st.info("Select a scenario and click **Analyze Change Set** in the sidebar to begin.")
        return

    active_scenario = load_scenario_definition(active_report.scenario_id)

    def _on_apply_fix() -> None:
        _trigger_analysis(run_post_fix=True, force_recompute=True)
        st.rerun()

    def _on_switch_safe() -> None:
        st.session_state["selected_scenario_id"] = "safe_optional_field"
        st.session_state["post_fix_enabled"] = False
        _trigger_analysis(run_post_fix=False, force_recompute=False)
        st.rerun()

    def _on_rerun_tests() -> None:
        _trigger_analysis(run_post_fix=st.session_state["post_fix_enabled"], force_recompute=True)
        st.rerun()

    tab_overview, tab_prs, tab_contract, tab_evidence, tab_ai = st.tabs(
        [
            "1. Overview",
            "2. PR Changes (Diffs)",
            "3. Dependency & Contract",
            "4. Test Evidence",
            "5. AI Explanation (ADK)",
        ]
    )

    with tab_overview:
        render_overview_tab(
            report=active_report,
            on_apply_fix=_on_apply_fix,
            on_switch_safe=_on_switch_safe,
        )

    with tab_prs:
        render_pr_changes_tab(report=active_report, scenario=active_scenario)

    with tab_contract:
        render_contract_tab(report=active_report, scenario=active_scenario)

    with tab_evidence:
        render_test_evidence_tab(
            report=active_report,
            on_rerun_tests=_on_rerun_tests,
            on_apply_fix=_on_apply_fix,
        )

    with tab_ai:
        render_ai_explanation_tab(
            report=active_report,
            on_regenerate_ai=_on_rerun_tests,
        )


if __name__ == "__main__":
    main()
