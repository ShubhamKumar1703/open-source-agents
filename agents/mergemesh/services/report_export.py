"""JSON and Markdown report export utilities for MergeMesh."""

from __future__ import annotations

import json
from pathlib import Path

from config.settings import REPORTS_ROOT
from domain.models import AnalysisReport


def export_report_to_json(report: AnalysisReport) -> str:
    """Serialize an AnalysisReport to formatted JSON."""
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def export_report_to_markdown(report: AnalysisReport) -> str:
    """Format an AnalysisReport as a developer-friendly Markdown document."""
    lines: list[str] = [
        f"# MergeMesh Compatibility Report — `{report.scenario_id}`",
        "",
        "> **Input Mode:** Synthetic local PR fixture (`simulated_fixture`). No live GitHub API call was made.",
        "",
        f"- **Run ID:** `{report.run_id}`",
        f"- **Timestamp (UTC):** `{report.created_at}`",
        f"- **Provider Repo:** `{report.provider_repo}` ({report.pr_a_title})",
        f"- **Consumer Repo:** `{report.consumer_repo}` ({report.pr_b_title})",
        f"- **Overall Verdict:** **{report.overall_status}**",
        f"- **AI Explanation Mode:** `{report.ai_explanation.provider}` (`status={report.ai_explanation.status}`)",
        "",
        "---",
        "",
        "## 1. Executed Test Evidence",
        "",
        "| Check | Kind | Status | Exit Code | Duration (ms) | File Path | Notes |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for tr in report.test_results:
        note = "Uses stale mock" if tr.uses_stale_mock else (tr.failure_summary or "Passed")
        lines.append(
            f"| {tr.label} | `{tr.test_kind}` | **{tr.status}** | `{tr.exit_code}` | {tr.duration_ms} | `{tr.file_path}` | {note} |"
        )
    if report.post_fix_result:
        pf = report.post_fix_result
        lines.append(
            f"| {pf.label} | `{pf.test_kind}` | **{pf.status}** | `{pf.exit_code}` | {pf.duration_ms} | `{pf.file_path}` | Remediated consumer check |"
        )

    lines.extend(["", "## 2. Deterministic Compatibility Findings", ""])
    if not report.finding_list:
        lines.append("No breaking compatibility findings detected. Candidate changes are compatible.")
    else:
        for f in report.finding_list:
            lines.extend(
                [
                    f"### [{f.severity}] `{f.finding_id}` — {f.kind}",
                    f"- **Verification State:** `{f.verification_state}`",
                    f"- **Summary:** {f.summary}",
                    f"- **Incompatible Field:** `{f.field}`"
                    + (f" (Candidate replacement: `{f.replacement_candidate}`)" if f.replacement_candidate else ""),
                    f"- **Suggested Remediation:** {f.suggested_remediation}",
                    "- **Measured Evidence:**",
                ]
            )
            for ev in f.evidence:
                loc = f"`{ev.path}:{ev.line}`" if ev.line else f"`{ev.path}`"
                lines.append(f"  - `[{ev.kind}]` ({loc}): {ev.detail}")
            lines.append("")

    lines.extend(
        [
            "## 3. AI Interpretation & Remediation Options",
            "",
            f"> **Source:** {report.ai_explanation.provider} | **Status:** `{report.ai_explanation.status}`",
            "",
            f"**Summary:** {report.ai_explanation.summary}",
            "",
            f"**Why It Matters:** {report.ai_explanation.why_it_matters}",
            "",
            "**Suggested Fix Options:**",
        ]
    )
    for opt in report.ai_explanation.suggested_fix_options:
        lines.append(f"- {opt}")

    return "\n".join(lines) + "\n"


def save_report_artifacts(
    report: AnalysisReport,
    reports_root: Path = REPORTS_ROOT,
) -> tuple[Path, Path]:
    """Write JSON and Markdown report files to the reports directory."""
    reports_root.mkdir(parents=True, exist_ok=True)
    json_path = reports_root / f"report_{report.scenario_id}.json"
    md_path = reports_root / f"report_{report.scenario_id}.md"
    json_path.write_text(export_report_to_json(report), encoding="utf-8")
    md_path.write_text(export_report_to_markdown(report), encoding="utf-8")
    return json_path, md_path
