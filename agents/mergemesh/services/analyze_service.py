"""End-to-end analysis coordinator for MergeMesh."""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agents.workflow import build_deterministic_explanation, generate_ai_explanation
from analysis.compatibility import evaluate_compatibility
from analysis.consumer_usage import scan_consumer_file
from analysis.contract_extractor import compare_contract_files
from analysis.dependency_graph import build_dependency_edges, build_graphviz_dot
from analysis.evidence import compute_scenario_hashes
from config.settings import FIXTURES_ROOT, AppSettings, load_settings
from domain.models import AnalysisReport, TestExecutionResult
from execution.runner import run_registered_test, run_scenario_test_suite
from services.scenario_loader import load_scenario_definition

REPORT_SCHEMA_VERSION = "1.0.0"


def compute_analysis_cache_key(scenario_id: str, settings: AppSettings, include_ai: bool, run_post_fix: bool) -> str:
    """Create a deterministic cache key from scenario input hashes and active provider mode."""
    scenario = load_scenario_definition(scenario_id, fixtures_root=settings.fixtures_root)
    s_root = settings.fixtures_root / scenario.id
    rel_paths = [
        scenario.provider_before,
        scenario.provider_after,
        scenario.consumer_source,
        scenario.provider_test,
        scenario.consumer_test,
        scenario.integration_test,
    ]
    hashes = compute_scenario_hashes(s_root, rel_paths)
    raw = (
        f"{scenario_id}|{settings.ai_mode}|{settings.ollama_model}|{settings.gemini_model}|"
        f"{settings.allow_local_fallback}|{include_ai}|{run_post_fix}|{ sorted(hashes.items()) }"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _derive_overall_status(
    findings_count: int,
    test_results: list[TestExecutionResult],
    post_fix_result: TestExecutionResult | None,
) -> str:
    if post_fix_result and post_fix_result.status == "PASS":
        return "REMEDIATED_PASS"

    combined = next((t for t in test_results if t.test_kind == "combined_integration"), None)
    if combined:
        if combined.status == "FAIL":
            return "BREAKING_FAILURE_REPRODUCED"
        if combined.status in {"ERROR", "TIMEOUT"}:
            return "TEST_EXECUTION_ERROR"
        if combined.status == "PASS" and findings_count == 0:
            return "COMPATIBLE_PASS"

    if findings_count > 0:
        return "POTENTIAL_COMPATIBILITY_ISSUE"
    return "COMPATIBLE_PASS"


def run_scenario_analysis(
    scenario_id: str,
    settings: AppSettings | None = None,
    fixtures_root: Path = FIXTURES_ROOT,
    include_ai: bool = True,
    run_post_fix: bool = False,
) -> AnalysisReport:
    """Execute deterministic contract analysis, allow-listed tests, and ADK explanation."""
    active_settings = settings or load_settings()
    scenario = load_scenario_definition(scenario_id, fixtures_root=fixtures_root)
    s_root = (fixtures_root / scenario.id).resolve()

    # 1. Deterministic contract comparison + AST consumer scan
    det_start_ns = time.perf_counter_ns()
    comparison = compare_contract_files(
        before_file=s_root / scenario.provider_before,
        after_file=s_root / scenario.provider_after,
        before_rel_path=scenario.provider_before,
        after_rel_path=scenario.provider_after,
        required_fields=scenario.required_fields,
    )
    consumer_accesses = scan_consumer_file(
        file_path=s_root / scenario.consumer_source,
        consumer_repo=scenario.consumer_repo,
        rel_file_path=scenario.consumer_source,
    )
    det_duration_ms = int((time.perf_counter_ns() - det_start_ns) / 1_000_000)

    # 2. Controlled test execution (isolated provider, isolated consumer, combined integration)
    test_start_ns = time.perf_counter_ns()
    test_results = run_scenario_test_suite(
        scenario_id=scenario.id,
        fixtures_root=fixtures_root,
        timeout_seconds=active_settings.test_runner_timeout_seconds,
    )
    post_fix_result: TestExecutionResult | None = None
    if run_post_fix and scenario.post_fix_test:
        post_fix_result = run_registered_test(
            test_id=f"{scenario.id}:post_fix_integration",
            fixtures_root=fixtures_root,
            timeout_seconds=active_settings.test_runner_timeout_seconds,
        )
    test_duration_ms = int((time.perf_counter_ns() - test_start_ns) / 1_000_000)

    combined_test = next((t for t in test_results if t.test_kind == "combined_integration"), None)

    # 3. Evaluate compatibility findings with executed test evidence
    findings = evaluate_compatibility(
        provider_repo=scenario.provider_repo,
        consumer_repo=scenario.consumer_repo,
        contract_comparison=comparison,
        consumer_accesses=consumer_accesses,
        combined_test_result=combined_test,
        suggested_fix_override=scenario.suggested_fix,
    )

    # 4. Dependency graph
    post_fix_passed = bool(post_fix_result and post_fix_result.status == "PASS")
    graph_edges = build_dependency_edges(
        provider_repo=scenario.provider_repo,
        consumer_repo=scenario.consumer_repo,
        interface_type=scenario.interface_type,
    )
    graph_dot = build_graphviz_dot(
        provider_repo=scenario.provider_repo,
        consumer_repo=scenario.consumer_repo,
        findings=findings,
        post_fix_passed=post_fix_passed,
    )

    # 5. Model-assisted explanation (or deterministic fallback)
    if include_ai:
        ai_explanation = generate_ai_explanation(
            settings=active_settings,
            scenario=scenario,
            comparison=comparison,
            consumer_accesses=consumer_accesses,
            findings=findings,
            test_results=test_results,
        )
    else:
        ai_explanation = build_deterministic_explanation(
            scenario=scenario,
            comparison=comparison,
            consumer_accesses=consumer_accesses,
            findings=findings,
            test_results=test_results,
            status="SKIPPED",
            provider_label="Deterministic Summary (AI skipped)",
            model_id=None,
            fallback_reason="AI explanation skipped by caller.",
        )

    rel_paths = [
        scenario.provider_before,
        scenario.provider_after,
        scenario.consumer_source,
        scenario.provider_source,
        scenario.provider_test,
        scenario.consumer_test,
        scenario.integration_test,
    ]
    if scenario.post_fix_test:
        rel_paths.append(scenario.post_fix_test)
    if scenario.post_fix_consumer_source:
        rel_paths.append(scenario.post_fix_consumer_source)
    for d in scenario.diffs:
        rel_paths.append(d.path)

    source_hashes = compute_scenario_hashes(s_root, rel_paths)
    overall_status = _derive_overall_status(len(findings), test_results, post_fix_result)

    return AnalysisReport(
        schema_version=REPORT_SCHEMA_VERSION,
        scenario_id=scenario.id,
        scenario_title=scenario.title,
        scenario_description=scenario.description,
        run_id=f"run_{uuid.uuid4().hex[:10]}",
        created_at=datetime.now(timezone.utc).isoformat(),
        input_mode="simulated_fixture",
        provider_repo=scenario.provider_repo,
        consumer_repo=scenario.consumer_repo,
        pr_a_title=scenario.pr_a_title,
        pr_b_title=scenario.pr_b_title,
        consumer_mock_note=scenario.consumer_mock_note,
        contract_comparison=comparison,
        consumer_accesses=consumer_accesses,
        finding_list=findings,
        test_results=test_results,
        post_fix_result=post_fix_result,
        graph_edges=graph_edges,
        graph_dot=graph_dot,
        diffs=scenario.diffs,
        ai_explanation=ai_explanation,
        overall_status=overall_status,
        deterministic_duration_ms=det_duration_ms,
        test_duration_ms=test_duration_ms,
        source_hashes=source_hashes,
    )
