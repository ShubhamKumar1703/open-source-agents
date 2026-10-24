"""Automated tests for reusable local Git repository analysis (independent of bundled fixtures)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from analysis.contract_extractor import ContractAnalysisError
from analysis.git_inspector import RepoInspectionError
from config.settings import load_settings
from services.analyze_service import (
    CustomRepoAnalysisRequest,
    run_custom_repo_analysis,
)


def _git(cwd: Path, *args: str) -> None:
    res = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    assert res.returncode == 0


def _init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init")
    _git(repo_dir, "config", "user.email", "test@mergemesh.local")
    _git(repo_dir, "config", "user.name", "MergeMesh Test")


def test_custom_git_repos_breaking_rename_static_and_reproduced_and_postfix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create two temporary Git repositories ('billing-service' and 'invoice-worker')
    with a custom directory layout and field rename ('account_uuid' -> 'accountUuid').
    Verify:
    1. Without test approval, static analysis detects INFERRED_RISK and does NOT execute user tests.
    2. With explicit test approval, the combined test fails with KeyError: 'account_uuid' -> REPRODUCED_FAILURE.
    3. Post-fix verification executes the remediated test and passes -> REMEDIATED_PASS.
    """
    monkeypatch.setenv("MERGEMESH_AI_MODE", "deterministic")
    settings = load_settings()

    provider_repo = tmp_path / "billing-service"
    consumer_repo = tmp_path / "invoice-worker"
    _init_git_repo(provider_repo)
    _init_git_repo(consumer_repo)

    # Provider commit 1 (base): uses 'account_uuid'
    schema_dir = provider_repo / "schemas" / "v1"
    schema_dir.mkdir(parents=True)
    (schema_dir / "account.json").write_text(
        json.dumps({"account_uuid": "acc_777", "tier": "enterprise"}),
        encoding="utf-8",
    )
    (provider_repo / "billing_api.py").write_text(
        'def get_billing_account() -> dict:\n    return {"account_uuid": "acc_777", "tier": "enterprise"}\n',
        encoding="utf-8",
    )
    _git(provider_repo, "add", ".")
    _git(provider_repo, "commit", "-m", "Initial v1 billing contract")

    # Provider commit 2 (HEAD): renames 'account_uuid' -> 'accountUuid'
    (schema_dir / "account.json").write_text(
        json.dumps({"accountUuid": "acc_777", "tier": "enterprise"}),
        encoding="utf-8",
    )
    (provider_repo / "billing_api.py").write_text(
        'def get_billing_account() -> dict:\n    return {"accountUuid": "acc_777", "tier": "enterprise"}\n',
        encoding="utf-8",
    )
    _git(provider_repo, "add", ".")
    _git(provider_repo, "commit", "-m", "Rename account_uuid to accountUuid")

    # Consumer repo commit 1 (HEAD): nested layout 'jobs/invoice_sync.py' reading 'account_uuid'
    jobs_dir = consumer_repo / "jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "invoice_sync.py").write_text(
        "def extract_account(payload: dict) -> str:\n"
        '    return payload["account_uuid"]\n',
        encoding="utf-8",
    )
    (jobs_dir / "invoice_sync_fixed.py").write_text(
        "def extract_account(payload: dict) -> str:\n"
        '    return payload.get("accountUuid") or payload["account_uuid"]\n',
        encoding="utf-8",
    )
    tests_dir = consumer_repo / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_integration.py").write_text(
        "from billing_api import get_billing_account\n"
        "from jobs.invoice_sync import extract_account\n\n"
        "def test_billing_to_invoice_sync():\n"
        "    assert extract_account(get_billing_account()) == 'acc_777'\n",
        encoding="utf-8",
    )
    (tests_dir / "test_integration_fixed.py").write_text(
        "from billing_api import get_billing_account\n"
        "from jobs.invoice_sync_fixed import extract_account\n\n"
        "def test_billing_to_invoice_sync_fixed():\n"
        "    assert extract_account(get_billing_account()) == 'acc_777'\n",
        encoding="utf-8",
    )
    _git(consumer_repo, "add", ".")
    _git(consumer_repo, "commit", "-m", "Add invoice sync worker and tests")

    # 1. Run WITHOUT user_approved_test_execution -> static INFERRED_RISK, test is NOT_RUN
    req_static = CustomRepoAnalysisRequest(
        provider_repo_path=str(provider_repo),
        consumer_repo_path=str(consumer_repo),
        provider_base_ref="HEAD~1",
        provider_target_ref="HEAD",
        consumer_target_ref="HEAD",
        user_approved_test_execution=False,
        combined_test_rel_path="tests/test_integration.py",
    )
    report_static = run_custom_repo_analysis(req_static, settings=settings)

    assert report_static.input_mode == "local_git_repos"
    assert report_static.provider_repo == "billing-service"
    assert report_static.consumer_repo == "invoice-worker"
    assert report_static.overall_status == "POTENTIAL_COMPATIBILITY_ISSUE"
    assert len(report_static.finding_list) == 1
    f_static = report_static.finding_list[0]
    assert f_static.field == "account_uuid"
    assert f_static.replacement_candidate == "accountUuid"
    assert f_static.verification_state == "INFERRED_RISK"
    assert report_static.test_results[0].status == "NOT_RUN"

    # 2. Run WITH explicit user_approved_test_execution=True -> test runs and FAILS -> REPRODUCED_FAILURE
    req_exec = CustomRepoAnalysisRequest(
        provider_repo_path=str(provider_repo),
        consumer_repo_path=str(consumer_repo),
        provider_base_ref="HEAD~1",
        provider_target_ref="HEAD",
        consumer_target_ref="HEAD",
        user_approved_test_execution=True,
        combined_test_rel_path="tests/test_integration.py",
        post_fix_test_rel_path="tests/test_integration_fixed.py",
        run_post_fix=False,
    )
    report_exec = run_custom_repo_analysis(req_exec, settings=settings)
    assert report_exec.overall_status == "BREAKING_FAILURE_REPRODUCED"
    assert report_exec.test_results[0].status == "FAIL"
    assert "account_uuid" in report_exec.test_results[0].failure_summary
    assert report_exec.finding_list[0].verification_state == "REPRODUCED_FAILURE"

    # 3. Run post-fix verification -> REMEDIATED_PASS
    req_exec.run_post_fix = True
    report_fixed = run_custom_repo_analysis(req_exec, settings=settings)
    assert report_fixed.post_fix_result is not None
    assert report_fixed.post_fix_result.status == "PASS"
    assert report_fixed.overall_status == "REMEDIATED_PASS"


def test_custom_git_repos_python_provider_contract_extraction_and_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that MergeMesh can extract before/after contracts directly from a Python (.py)
    provider module across named Git branches ('main' vs 'feat/camel-sku') with zero .json files.
    """
    monkeypatch.setenv("MERGEMESH_AI_MODE", "deterministic")
    settings = load_settings()

    prov_repo = tmp_path / "catalog-api"
    cons_repo = tmp_path / "search-indexer"
    _init_git_repo(prov_repo)
    _init_git_repo(cons_repo)

    # Provider on branch 'base-branch': Python function returning dict literal
    src_dir = prov_repo / "src" / "catalog"
    src_dir.mkdir(parents=True)
    (src_dir / "endpoints.py").write_text(
        'def fetch_item(sku: str = "SKU-900") -> dict:\n'
        '    return {"sku_code": sku, "in_stock": True}\n',
        encoding="utf-8",
    )
    _git(prov_repo, "checkout", "-b", "base-branch")
    _git(prov_repo, "add", ".")
    _git(prov_repo, "commit", "-m", "Base Python endpoint contract")

    # Provider on branch 'feat/camel-sku': renames 'sku_code' -> 'skuCode'
    _git(prov_repo, "checkout", "-b", "feat/camel-sku")
    (src_dir / "endpoints.py").write_text(
        'def fetch_item(sku: str = "SKU-900") -> dict:\n'
        '    return {"skuCode": sku, "in_stock": True}\n',
        encoding="utf-8",
    )
    _git(prov_repo, "add", ".")
    _git(prov_repo, "commit", "-m", "Rename sku_code to skuCode in Python endpoint")

    # Consumer repo with deeply nested Python worker
    worker_dir = cons_repo / "pkg" / "indexer"
    worker_dir.mkdir(parents=True)
    (worker_dir / "worker.py").write_text(
        "def index_catalog_item(item: dict) -> str:\n"
        '    return item["sku_code"]\n',
        encoding="utf-8",
    )
    _git(cons_repo, "add", ".")
    _git(cons_repo, "commit", "-m", "Initial search indexer worker")

    req = CustomRepoAnalysisRequest(
        provider_repo_path=str(prov_repo),
        consumer_repo_path=str(cons_repo),
        provider_base_ref="base-branch",
        provider_target_ref="feat/camel-sku",
        consumer_target_ref="HEAD",
    )
    report = run_custom_repo_analysis(req, settings=settings)

    assert len(report.finding_list) == 1
    finding = report.finding_list[0]
    assert finding.finding_id == "missing_sku_code"
    assert finding.field == "sku_code"
    assert finding.replacement_candidate == "skuCode"
    assert finding.provider_repo == "catalog-api"
    assert finding.consumer_repo == "search-indexer"


def test_custom_git_repos_safe_additive_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a compatible additive field change in temporary Git repos produces zero findings."""
    monkeypatch.setenv("MERGEMESH_AI_MODE", "deterministic")
    settings = load_settings()

    prov_repo = tmp_path / "payments-service"
    cons_repo = tmp_path / "receipt-mailer"
    _init_git_repo(prov_repo)
    _init_git_repo(cons_repo)

    (prov_repo / "payment.json").write_text(
        json.dumps({"payment_id": "pay_1", "amount": 500}),
        encoding="utf-8",
    )
    _git(prov_repo, "add", ".")
    _git(prov_repo, "commit", "-m", "Initial payment contract")

    # Add optional 'tax_rate' while keeping 'payment_id' and 'amount'
    (prov_repo / "payment.json").write_text(
        json.dumps({"payment_id": "pay_1", "amount": 500, "tax_rate": 0.18}),
        encoding="utf-8",
    )
    _git(prov_repo, "add", ".")
    _git(prov_repo, "commit", "-m", "Add optional tax_rate field")

    (cons_repo / "mailer.py").write_text(
        'def format_receipt(data: dict) -> str:\n    return f"{data[\'payment_id\']}: {data[\'amount\']}"\n',
        encoding="utf-8",
    )
    _git(cons_repo, "add", ".")
    _git(cons_repo, "commit", "-m", "Initial receipt mailer")

    req = CustomRepoAnalysisRequest(
        provider_repo_path=str(prov_repo),
        consumer_repo_path=str(cons_repo),
        provider_base_ref="HEAD~1",
        provider_target_ref="HEAD",
    )
    report = run_custom_repo_analysis(req, settings=settings)

    assert report.overall_status == "COMPATIBLE_PASS"
    assert report.finding_list == []
    assert "tax_rate" in report.contract_comparison.added_fields


def test_custom_git_repos_error_handling_invalid_paths_revisions_and_contracts(
    tmp_path: Path,
) -> None:
    """Verify clear errors for:
    - Missing or non-Git repository paths
    - Invalid or unavailable Git revisions
    - Unsupported or unextractable contract files
    """
    # 1. Missing repository path
    with pytest.raises(RepoInspectionError, match="does not exist"):
        run_custom_repo_analysis(
            CustomRepoAnalysisRequest(
                provider_repo_path=str(tmp_path / "nonexistent_repo"),
                consumer_repo_path=str(tmp_path),
            )
        )

    # 2. Directory that is not a Git repository
    plain_dir = tmp_path / "plain_folder"
    plain_dir.mkdir()
    with pytest.raises(RepoInspectionError, match="not a valid Git repository"):
        run_custom_repo_analysis(
            CustomRepoAnalysisRequest(
                provider_repo_path=str(plain_dir),
                consumer_repo_path=str(plain_dir),
            )
        )

    # 3. Invalid / unavailable Git revision
    valid_repo = tmp_path / "valid-repo"
    _init_git_repo(valid_repo)
    (valid_repo / "util.py").write_text("x = 42\n", encoding="utf-8")
    _git(valid_repo, "add", ".")
    _git(valid_repo, "commit", "-m", "Commit 1")

    with pytest.raises(RepoInspectionError, match="Invalid or unavailable Git revision"):
        run_custom_repo_analysis(
            CustomRepoAnalysisRequest(
                provider_repo_path=str(valid_repo),
                consumer_repo_path=str(valid_repo),
                provider_base_ref="nonexistent-branch-999",
                provider_target_ref="HEAD",
            )
        )

    # 4. Unextractable contract in repository (util.py has no dict return or model class)
    (valid_repo / "util.py").write_text("x = 43\n", encoding="utf-8")
    _git(valid_repo, "add", ".")
    _git(valid_repo, "commit", "-m", "Commit 2")

    with pytest.raises(ContractAnalysisError, match="Could not auto-discover an extractable"):
        run_custom_repo_analysis(
            CustomRepoAnalysisRequest(
                provider_repo_path=str(valid_repo),
                consumer_repo_path=str(valid_repo),
                provider_base_ref="HEAD~1",
                provider_target_ref="HEAD",
            )
        )
