# MergeMesh — Cross-Repository Change-Set Compatibility Agent

> A local-first cross-repository compatibility analyzer and Google ADK agent workflow that inspects local Git repositories (or bundled PR scenarios), compares REST JSON and Python provider contracts across Git revisions, scans consumer Python AST field usage, optionally executes user-approved integration tests, and provides verified remediation guidance with zero API credit requirements.

---

## Overview

### Problem Statement & Intended Users
- **Problem**: Cloud and microservice applications frequently span multiple independently managed repositories (for example, a backend REST API repository and a downstream client or worker repository). Each repository's pull request can pass its own isolated CI suite—especially when consumer unit tests rely on stale or hardcoded mocks—yet fail at runtime when the candidate revisions are combined.
- **Intended Users**: Backend, platform, and DevOps engineers working across multiple local Python/REST repositories who want deterministic, evidence-backed compatibility checks before merging candidate branches, without sending code to external cloud APIs or requiring paid LLM credits.
- **Complementary Positioning**: MergeMesh does **not** claim to replace GitHub Merge Queues, GitLab Merge Trains, Pact consumer-driven contract testing, or full staging pipelines. It provides a focused, local-first workflow for correlating candidate Git revisions across two repositories when explicit cross-repo contract pipelines are missing or incomplete.

### Key Features
- **Reusable Local Git Repository Analyzer**: Point MergeMesh at any local provider Git repository and consumer Git repository, specify base and proposed Git revisions (`HEAD~1`, `main`, branch names, commit SHAs, or `WORKTREE`), and analyze real repository changes (`analysis/git_inspector.py`, `services/analyze_service.py`).
- **Dual Contract Extraction (`.json` & `.py` AST)**: Extracts before/after provider response shapes from JSON contract/schema files (`.json`) or directly from Python provider source files (`.py` functions returning dictionary literals or annotated `BaseModel` / `TypedDict` / `@dataclass` classes) via `analysis/contract_extractor.py`.
- **Python AST Consumer Usage Scanner**: Scans consumer Python files (`analysis/consumer_usage.py`) using Python's `ast` module to locate dictionary subscript (`payload["field"]`), nested subscript (`data["customer"]["id"]`), and `.get("field")` accesses with exact file paths, line numbers, and code snippets.
- **Strict Static vs. Reproduced Failure Distinction**:
  - `INFERRED_RISK` (*"Potential compatibility issue"*): Reported when static contract diffing and consumer AST analysis detect a breaking mismatch but no runtime integration test has been executed.
  - `REPRODUCED_FAILURE` (*"Reproduced integration failure"*): Reported only when an explicitly approved integration test executes and fails (`exit_code=1`).
- **Opt-In Safe Test Execution**: Bundled fixture tests run from an internal allow-list (`execution/fixture_registry.py`), while custom repository `pytest` files require explicit opt-in approval (`user_approved_test_execution=True`) before running in a bounded local subprocess (`execution/runner.py`).
- **Local-First Google ADK & Ollama Integration**: Uses Google Agent Development Kit (`google-adk`) with `LiteLlm(model="ollama_chat/gemma3:4b")` when local Ollama is running on `localhost:11434`, and automatically falls back to the built-in **Deterministic Local Rule Engine** when Ollama is offline or `deterministic` mode is selected.

### Implemented vs. Planned Capabilities

| Capability | Status | Notes |
| :--- | :--- | :--- |
| Reusable local Git repository & revision analysis (`base_ref` $\rightarrow$ `target_ref` / `WORKTREE`) | **Implemented** | Supports any local Git repositories, branches, tags, commit SHAs, or working tree |
| Deterministic JSON (`.json`) and Python AST (`.py`) provider contract extraction | **Implemented** | Detects removed fields, added fields, type changes, missing required fields, candidate renames |
| Python AST consumer field access scanner (single file or repo-wide auto-discovery) | **Implemented** | Detects `payload["key"]`, nested subscripts, and `payload.get("key")` |
| Opt-in `pytest` runner for custom repos + allow-listed runner for bundled scenarios | **Implemented** | Captures exit code, duration, `stdout`, `stderr`, and exception summary |
| Streamlit 5-tab dashboard with both Custom Git Repo mode & Bundled Scenarios | **Implemented** | Works completely offline with session-state caching and JSON/Markdown export |
| Google ADK agent with local Gemma via Ollama + deterministic fallback | **Implemented** | Local-only runtime (`localhost:11434`); zero external network calls or API keys |
| Remote GitHub URL cloning / webhook ingestion | **Planned** | Operates on local Git checkouts; remote cloning is not automated |
| Non-Python consumer AST parsers (TypeScript/Go) & OpenAPI/Protobuf | **Planned** | Currently scoped to Python AST and JSON/Python dict contracts |

---

## Technical Stack

- **Language & Runtime**: Python 3.10+ (tested on Python 3.10 and Python 3.13)
- **Agent Framework / Core Library**: Google Agent Development Kit (`google-adk`), `litellm`, `pydantic`
- **Models**: Local `gemma3:4b` (or `gemma2:2b`) via Ollama (`ollama_chat/gemma3:4b`), plus built-in **Deterministic Local Rule Engine** (requires no LLM runtime)
- **UI & Visualization**: Streamlit (`streamlit`), Graphviz DOT dependency graphs
- **Test & Analysis Stack**: `pytest`, `fastapi`, `httpx`, Git CLI (`git rev-parse`, `git show`, `git diff`, `git ls-tree`), Python standard library (`ast`, `json`, `subprocess`, `hashlib`)

---

## Architecture & Data Flow

```text
[Streamlit UI (app.py / ui/)]  OR  [Python API (run_custom_repo_analysis / run_scenario_analysis)]
        |
        v
[Analysis Coordinator (services/analyze_service.py)]
        |
        +---> [1. Input Loader]
        |         - Custom Git Repos: analysis/git_inspector.py (git rev-parse, git show, git diff)
        |         - Bundled Scenarios: services/scenario_loader.py (fixtures/scenarios/<id>/)
        |
        +---> [2. Deterministic Contract Extractor (analysis/contract_extractor.py)]
        |         Extracts & diffs .json or .py contracts across base_ref and target_ref
        |
        +---> [3. Python AST Consumer Scanner (analysis/consumer_usage.py)]
        |         Extracts payload["field"] & payload.get("field") with file paths & line numbers
        |
        +---> [4. Controlled Pytest Runner (execution/runner.py)]
        |         Runs allow-listed fixture tests OR explicitly approved user repo pytest files
        |
        +---> [5. Compatibility Evaluator (analysis/compatibility.py)]
        |         Produces Findings (INFERRED_RISK vs REPRODUCED_FAILURE) & EvidenceItems
        |
        +---> [6. Local ADK Workflow (agents/workflow.py)]
                  Invokes local Ollama Gemma via ADK LiteLlm (if running on localhost)
                  OR synthesizes Deterministic Explanation (if Ollama is offline / deterministic mode)
        |
        v
[Typed AnalysisReport (domain/models.py)] ---> [Dashboard Tabs + JSON/Markdown Export]
```

### Supported Languages, Contract Formats & Analysis Rules
- **Provider Contract Formats**:
  - **JSON (`.json`)**: Sample REST JSON object payloads or JSON Schema objects containing `properties` and optional `required` arrays.
  - **Python (`.py`)**: Provider functions returning dictionary literals (`return {"customer_id": cid, "status": "active"}`) or classes (`BaseModel`, `TypedDict`, `@dataclass`) with type-annotated attributes.
- **Consumer Source Language**:
  - **Python (`.py`)**: Static string dictionary subscripts (`payload["field"]`, `data["nested"]["field"]`) and `.get("field")` calls parsed via Python `ast`.
- **Compatibility Rules**:
  1. `FIELD_REMOVED_BUT_CONSUMED` (`HIGH`): A field present in `base_ref` is removed in `target_ref` while the consumer AST still accesses it.
  2. `MISSING_REQUIRED_FIELD` (`HIGH`): A declared required field is missing in `target_ref`.
  3. `FIELD_TYPE_CHANGED` (`HIGH` / `MEDIUM`): A field's value type changes incompatibly (e.g., `integer` $\rightarrow$ `string`) between `base_ref` and `target_ref`.
  4. **Candidate Rename Detection**: Heuristic matching of removed and added fields at the same nesting level (`customer_id` $\leftrightarrow$ `customerId`).

---

## Prerequisites

- **Python**: Python `3.10` or higher (`python --version`)
- **Git**: `git` CLI available in `PATH` (`git --version`) for analyzing local Git repositories and revisions
- **Package Manager**: `pip` (`python -m pip --version`)
- **API Keys / Cloud Credits**: **None required.** MergeMesh runs 100% locally without any API keys or cloud credentials.
- **Optional Local LLM Runtime**: [Ollama](https://ollama.com/) installed locally with `gemma3:4b` pulled (`ollama pull gemma3:4b`) if you wish to test live local LLM generation alongside the deterministic engine.

---

## Installation & Setup

1. **Navigate to the project directory:**
   ```bash
   cd agents/mergemesh
   ```

2. **Create and activate an isolated virtual environment:**
   - **Windows (PowerShell):**
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     python -m pip install --upgrade pip
     python -m pip install -r requirements.txt
     ```
   - **Linux / macOS:**
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     pip install --upgrade pip
     pip install -r requirements.txt
     ```

3. **Configure Environment Variables (Optional):**
   MergeMesh runs with safe local defaults out of the box. To customize settings, copy `.env.example` to `.env`:
   ```bash
   # Windows PowerShell:
   Copy-Item .env.example .env

   # Linux / macOS:
   cp .env.example .env
   ```

---

## Configuration & Environment Variables

| Variable Name | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `MERGEMESH_AI_MODE` | No | `local` | `local` (Local Gemma via Ollama + ADK with deterministic fallback) or `deterministic` (Pure Python engine, zero LLM calls) |
| `OLLAMA_BASE_URL` | No | `http://localhost:11434` | Local loopback URL for Ollama (`localhost` / `127.0.0.1` enforced) |
| `OLLAMA_MODEL` | No | `gemma3:4b` | Local Ollama model tag used by Google ADK `LiteLlm` |
| `ALLOW_LOCAL_FALLBACK` | No | `true` | When `true`, preserves the full report and uses deterministic explanation fallback if Ollama is offline |
| `TEST_RUNNER_TIMEOUT_SECONDS` | No | `15` | Maximum timeout in seconds for each `pytest` subprocess |
| `AI_REQUEST_TIMEOUT_SECONDS` | No | `25` | Maximum timeout in seconds for local Ollama inference requests |

---

## How to Run

### 1. Launch the Interactive Streamlit Dashboard
```bash
python -m streamlit run app.py
```

In the sidebar under **Analysis Input Source**, choose between:
- **Local Git Repositories (Custom Paths & Refs)**:
  1. Enter your local **Provider Repo Path** (e.g., `/path/to/orders-api`), **Base Git Ref** (e.g., `HEAD~1` or `main`), and **Proposed Git Ref** (e.g., `HEAD`, a feature branch name, or `WORKTREE`).
  2. Optionally enter the relative **Contract File Path** (`.json` or `.py`), or leave it blank to auto-discover.
  3. Enter your local **Consumer Repo Path** (e.g., `/path/to/checkout-client`) and **Consumer Target Ref** (e.g., `HEAD` or `WORKTREE`). Leave **Consumer Python File** blank to scan all non-test `.py` files in the consumer repo.
  4. *(Optional)* Open **3. Optional Pytest Execution (Explicit Approval)**, check **"I approve executing the configured pytest files locally"**, and provide relative `.py` test paths if you want MergeMesh to run tests and verify whether static risks are reproduced at runtime.
  5. Click **Analyze Change Set**.
- **Bundled Example Scenarios**:
  - Select **Scenario A — Breaking Field Rename (`customer_id` $\rightarrow$ `customerId`)** or **Scenario B — Compatible Optional Field Addition (`display_name`)** for an immediate, self-contained demonstration.

### 2. Analyze Any Local Git Repositories via Python CLI
```python
from services.analyze_service import CustomRepoAnalysisRequest, run_custom_repo_analysis

report = run_custom_repo_analysis(
    CustomRepoAnalysisRequest(
        provider_repo_path="/path/to/provider-repo",
        consumer_repo_path="/path/to/consumer-repo",
        provider_base_ref="HEAD~1",
        provider_target_ref="HEAD",
        consumer_target_ref="HEAD",
        user_approved_test_execution=False,  # Static analysis only by default
    )
)
print("Overall Verdict:", report.overall_status)
for finding in report.finding_list:
    print(f"[{finding.severity}] {finding.finding_id} ({finding.verification_state}): {finding.summary}")
```

---

## Example Input & Output

### Example 1: Analyzing Custom Local Git Repositories (`billing-service` & `invoice-worker`)
- **Input**:
  - `provider_repo_path`: `/tmp/billing-service` (`HEAD~1` $\rightarrow$ `HEAD`, where `schemas/v1/account.json` renames `"account_uuid"` $\rightarrow$ `"accountUuid"`)
  - `consumer_repo_path`: `/tmp/invoice-worker` (`HEAD`, where `jobs/invoice_sync.py` reads `payload["account_uuid"]`)
- **Output (Static Analysis, `user_approved_test_execution=False`)**:
  ```text
  Input Mode: local_git_repos
  Overall Verdict: POTENTIAL_COMPATIBILITY_ISSUE
  Finding: [HIGH] missing_account_uuid (FIELD_REMOVED_BUT_CONSUMED | verification_state=INFERRED_RISK)
  Summary: Potential compatibility issue: billing-service removed 'account_uuid' (renamed to 'accountUuid') while invoice-worker still reads 'account_uuid'.
  Evidence:
    - [provider_contract_before] billing-service@HEAD~1:schemas/v1/account.json
    - [provider_contract_after]  billing-service@HEAD:schemas/v1/account.json
    - [consumer_access]          jobs/invoice_sync.py:2 (return payload["account_uuid"])
  ```
- **Output (With Approved Integration Test, `user_approved_test_execution=True`)**:
  ```text
  Overall Verdict: BREAKING_FAILURE_REPRODUCED
  Finding: [HIGH] missing_account_uuid (verification_state=REPRODUCED_FAILURE)
  Combined Test: tests/test_integration.py -> FAIL (exit_code=1, KeyError: 'account_uuid')
  ```

### Example 2: Reproducing Bundled Scenario A (`customer_id_rename`) & Scenario B (`safe_optional_field`)
- **Scenario A (`customer_id_rename`)**:
  - Isolated tests `test_provider_unit.py` (`PASS`) and `test_consumer_unit.py` (`PASS` using stale mock) succeed, while `test_combined_contract.py` fails with `KeyError: 'customer_id'` (`BREAKING_FAILURE_REPRODUCED`). Clicking **Verify Suggested Fix** runs `test_post_fix_contract.py` and passes (`REMEDIATED_PASS`).
- **Scenario B (`safe_optional_field`)**:
  - Provider adds optional `"display_name"` while preserving `"customer_id"`. All isolated and combined checks pass with `0` findings (`COMPATIBLE_PASS`).

---

## How to Test

Run the complete 31-test automated verification suite (unit tests, bundled scenario integration tests, custom temporary Git repository tests, and headless Streamlit `AppTest` UI tests) from `agents/mergemesh`:

```bash
python -m pytest -v
```

---

## Known Limitations

- **Supported Contract & Language Scope**: Provider contract extraction supports JSON files (`.json`) and Python files (`.py`) containing functions that return dictionary literals or annotated model classes (`BaseModel`, `TypedDict`, `@dataclass`). Consumer usage scanning supports static string dictionary subscripts (`payload["key"]`) and `.get("key")` calls in Python `.py` files. Dynamic key lookup (`payload[var]`), complex runtime serialization, or non-Python languages are not parsed.
- **Subprocess Execution Boundary**: Opt-in test execution runs `python -m pytest` as a local OS subprocess (`shell=False`, sanitized environment variables, bounded timeout). It is **not** a container, VM, or kernel sandbox; only approve test execution on local repositories you trust.
- **Local Ollama Availability**: If Ollama is not running on `localhost:11434` or `gemma3:4b` is not pulled, MergeMesh does not claim local LLM generation occurred; it labels the explanation status as `FALLBACK` (or `SKIPPED` in `deterministic` mode) and uses the deterministic rule engine.

---

## Security & Privacy Considerations

- **Zero Secrets & Zero External Network Calls**: MergeMesh requires no API keys and never transmits repository code, diffs, contracts, test outputs, or prompts to external AI services. `config/settings.py` enforces loopback-only URLs (`localhost` / `127.0.0.1` / `::1`) for local Ollama checks.
- **Explicit Opt-In for User Repo Tests**: In Custom Git Repository mode, `user_approved_test_execution` defaults to `False`. MergeMesh never automatically executes code from analyzed repositories.
- **Command & Path Validation**: All Git and `pytest` commands run with `shell=False`, reject leading `-` flag injection on revision/path inputs, and reject `..` path traversal outside the repository root.

---

## Future Improvements

- [ ] Support OpenAPI 3.x YAML/JSON specifications and FastAPI `response_model` resolution across imported modules.
- [ ] Extend AST consumer scanners to TypeScript/JavaScript (`fetch` / `axios` response destructuring).
- [ ] Add optional Docker container isolation for executing untrusted repository test suites.

---

## License

*This project is contributed under the repository's open-source [MIT License](../../LICENSE).*
