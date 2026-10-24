"""Controlled subprocess test runner for allow-listed MergeMesh fixture tests."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from config.settings import FIXTURES_ROOT
from domain.enums import CheckStatus
from domain.models import TestExecutionResult
from execution.fixture_registry import resolve_registered_test_path

MAX_OUTPUT_CHARS = 6000


def _truncate_output(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [output truncated]"


def _extract_failure_summary(stdout: str, stderr: str) -> str:
    combined = f"{stdout}\n{stderr}"
    # Look for pytest 'E   KeyError: ...' or 'E   AssertionError: ...' lines
    for line in combined.splitlines():
        stripped = line.strip()
        if stripped.startswith("E   ") and len(stripped) > 4:
            return stripped[4:].strip()
    m = re.search(r"(KeyError:\s*[^\n]+|AssertionError:\s*[^\n]+|TypeError:\s*[^\n]+)", combined)
    if m:
        return m.group(1).strip()
    return ""


def _build_sanitized_env() -> dict[str, str]:
    """Construct a minimal environment for subprocess test runs with no API secrets."""
    safe_keys = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "COMSPEC",
        "PATHEXT",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    }
    clean_env: dict[str, str] = {}
    for k, v in os.environ.items():
        if k.upper() in safe_keys:
            clean_env[k] = v
    clean_env["PYTHONDONTWRITEBYTECODE"] = "1"
    clean_env["PYTHONIOENCODING"] = "utf-8"
    return clean_env


def run_registered_test(
    test_id: str,
    fixtures_root: Path = FIXTURES_ROOT,
    timeout_seconds: int = 15,
) -> TestExecutionResult:
    """Execute an allow-listed fixture test via pytest in a bounded subprocess."""
    try:
        entry, test_file = resolve_registered_test_path(test_id, fixtures_root=fixtures_root)
    except (PermissionError, FileNotFoundError) as exc:
        return TestExecutionResult(
            test_id=test_id,
            label=test_id,
            test_kind="unknown",
            status="ERROR",
            exit_code=None,
            duration_ms=0,
            stdout="",
            stderr=str(exc),
            file_path="",
            failure_summary=str(exc),
            expected_failure=False,
            uses_stale_mock=False,
        )

    scenario_root = (fixtures_root / entry.scenario_id).resolve()
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        "-v",
        "--tb=short",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ]

    start_ns = time.perf_counter_ns()
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(scenario_root),
            env=_build_sanitized_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
        )
        duration_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        stdout = _truncate_output(completed.stdout or "")
        stderr = _truncate_output(completed.stderr or "")
        code = completed.returncode

        # Pytest exit codes:
        # 0 = all tests passed
        # 1 = tests were collected and run but some failed
        # 2, 3, 4, 5 = internal error, usage error, or no tests collected
        if code == 0:
            status: CheckStatus = "PASS"
            failure_summary = ""
        elif code == 1:
            status = "FAIL"
            failure_summary = _extract_failure_summary(stdout, stderr) or "Assertion or runtime failure in test"
        else:
            status = "ERROR"
            failure_summary = f"Test runner error (pytest exit code {code})"

        return TestExecutionResult(
            test_id=entry.test_id,
            label=entry.label,
            test_kind=entry.test_kind,
            status=status,
            exit_code=code,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            file_path=entry.relative_path,
            failure_summary=failure_summary,
            expected_failure=entry.expected_failure,
            uses_stale_mock=entry.uses_stale_mock,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        out_str = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err_str = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return TestExecutionResult(
            test_id=entry.test_id,
            label=entry.label,
            test_kind=entry.test_kind,
            status="TIMEOUT",
            exit_code=None,
            duration_ms=duration_ms,
            stdout=_truncate_output(out_str),
            stderr=_truncate_output(err_str),
            file_path=entry.relative_path,
            failure_summary=f"Test timed out after {timeout_seconds}s",
            expected_failure=entry.expected_failure,
            uses_stale_mock=entry.uses_stale_mock,
        )
    except OSError as exc:
        duration_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        return TestExecutionResult(
            test_id=entry.test_id,
            label=entry.label,
            test_kind=entry.test_kind,
            status="ERROR",
            exit_code=None,
            duration_ms=duration_ms,
            stdout="",
            stderr=str(exc),
            file_path=entry.relative_path,
            failure_summary=f"Failed to start test runner: {exc}",
            expected_failure=entry.expected_failure,
            uses_stale_mock=entry.uses_stale_mock,
        )


def run_scenario_test_suite(
    scenario_id: str,
    fixtures_root: Path = FIXTURES_ROOT,
    timeout_seconds: int = 15,
) -> list[TestExecutionResult]:
    """Run isolated provider, isolated consumer, and combined integration checks for a scenario."""
    results: list[TestExecutionResult] = []
    for kind in ("provider_isolated", "consumer_isolated", "combined_integration"):
        tid = f"{scenario_id}:{kind}"
        results.append(
            run_registered_test(
                test_id=tid,
                fixtures_root=fixtures_root,
                timeout_seconds=timeout_seconds,
            )
        )
    return results
