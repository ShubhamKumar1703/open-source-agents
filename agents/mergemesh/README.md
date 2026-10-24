# MergeMesh — Cross-Repository Change-Set Compatibility Agent

> A local-first cross-repository compatibility analyzer and Google ADK agent workflow that correlates candidate pull requests across independently owned services, executes controlled integration tests to expose breaking contract changes hidden by stale mocks, and provides verified remediation guidance with zero API credit requirements.

---

## Overview

### Problem Statement & Intended Users
- **Problem**: Modern cloud applications often span multiple independently managed repositories (e.g., a backend REST API service and a downstream client service). Each repository's pull request can pass its own isolated unit tests—especially when consumer tests rely on hardcoded or stale mocks—yet break at runtime when deployed together. While single-repository merge queues/trains and explicit consumer-driven contract frameworks (such as Pact) solve parts of this problem, teams frequently encounter coverage gaps when explicit cross-repo contracts are incomplete or mocks drift from provider responses.
- **Intended Users**: Backend, platform, and DevOps engineers managing multi-repository services who want deterministic, executable pre-merge compatibility evidence without relying on external cloud APIs or paid LLM credits.
- **Complementary Positioning**: MergeMesh does **not** claim to replace GitHub Merge Queues, GitLab Merge Trains, Pact contract testing, or end-to-end staging pipelines. Instead, it explores a complementary workflow: correlating candidate changes across separate repositories, executing a targeted cross-repository compatibility check, and strictly separating observed runtime evidence from optional AI-generated explanations.

### Key Features
- **Deterministic REST JSON Contract Diffing**: Compares before and after provider JSON response snapshots (`analysis/contract_extractor.py`) to detect removed fields, added fields, candidate renames (`customer_id` $\rightarrow$ `customerId`), missing required fields, and incompatible type changes.
- **Python AST Consumer Usage Scanner**: Parses consumer Python source files (`analysis/consumer_usage.py`) using Python's `ast` module to pinpoint dictionary subscript (`payload["customer_id"]`), nested subscript (`data["customer"]["id"]`), and `.get("field")` accesses with exact file paths and line numbers.
- **Controlled Allow-Listed Test Execution**: Runs isolated provider tests, isolated consumer tests, combined cross-repository integration checks, and post-fix verification checks in a bounded `pytest` subprocess (`execution/runner.py`) with sanitized environment variables, `shell=False`, path-traversal protection, and timeouts.
- **Strict Fact vs. AI Separation**: Distinguishes `INFERRED_RISK` (*"Potential compatibility issue"* before test execution) from `REPRODUCED_FAILURE` (*"Reproduced integration failure"* after the combined test executes and fails).
- **Local-First Google ADK & Ollama Integration**: Uses Google Agent Development Kit (`google-adk`) with `LiteLlm(model="ollama_chat/gemma3:4b")` for optional local explanation synthesis when Ollama is running locally, and automatically falls back to a deterministic ground-truth explanation when Ollama is offline or not installed. **Requires zero API keys, zero cloud accounts, and zero hackathon API credits.**
- **Interactive Streamlit Engineering Dashboard**: 5-tab interface (`1. Overview`, `2. PR Changes (Diffs)`, `3. Dependency & Contract`, `4. Test Evidence`, `5. AI Explanation (ADK)`) with one-click post-fix verification and JSON/Markdown report export.

### Implemented vs. Planned Capabilities

| Capability | Status | Notes |
| :--- | :--- | :--- |
| Preloaded multi-repo PR fixture loader & path validator | **Implemented** | Validates `scenario.json` and prevents `..` path traversal |
| Deterministic before/after JSON contract comparison | **Implemented** | Detects field removal, addition, type change, missing required field, candidate rename |
| Python AST consumer field access detection | **Implemented** | Detects `payload["key"]`, nested subscripts, and `payload.get("key")` |
| Allow-listed `pytest` runner (isolated, combined, post-fix) | **Implemented** | Captures real exit code, duration, `stdout`, `stderr`, and exception summary |
| Streamlit 5-tab dashboard & JSON/Markdown report export | **Implemented** | Works completely offline with session-state caching |
| Google ADK agent with local Gemma via Ollama + deterministic fallback | **Implemented** | Local-only runtime (`localhost:11434`); zero external network calls |
| Live GitHub API / webhook PR ingestion | **Planned** | Deferred; current release uses local synthetic PR fixtures |
| Multi-language consumer AST parsers (TypeScript/Go) & OpenAPI/AsyncAPI | **Planned** | Currently scoped to Python AST and REST JSON snapshots |

---

## Technical Stack

- **Language & Runtime**: Python 3.10+ (tested on Python 3.10 and Python 3.13)
- **Agent Framework / Core Library**: Google Agent Development Kit (`google-adk`), `litellm`, `pydantic`
- **Models**: Local `gemma3:4b` (or `gemma2:2b`) via Ollama (`ollama_chat/gemma3:4b`), plus built-in **Deterministic Local Rule Engine** (requires no LLM runtime)
- **UI & Visualization**: Streamlit (`streamlit`), Graphviz DOT dependency graphs
- **Test & Fixture Stack**: `pytest`, `fastapi`, `httpx`, Python standard library (`ast`, `json`, `subprocess`, `hashlib`)

---

## Architecture & Data Flow

```text
[Streamlit UI (app.py / ui/)]
        |
        v
[Analysis Coordinator (services/analyze_service.py)]
        |
        +---> [1. Scenario Loader (services/scenario_loader.py)]
        |         Loads & validates local PR fixtures (fixtures/scenarios/<id>/)
        |
        +---> [2. Deterministic Contract Extractor (analysis/contract_extractor.py)]
        |         Diffs provider_before.json vs provider_after.json
        |
        +---> [3. Python AST Consumer Scanner (analysis/consumer_usage.py)]
        |         Extracts payload["field"] & payload.get("field") with line numbers
        |
        +---> [4. Controlled Pytest Runner (execution/runner.py)]
        |         Executes allow-listed isolated, combined, and post-fix tests
        |
        +---> [5. Compatibility Evaluator (analysis/compatibility.py)]
        |         Correlates contract diffs + AST usage + test exit codes -> Findings & Evidence
        |
        +---> [6. Local ADK Workflow (agents/workflow.py)]
                  Invokes local Ollama Gemma via ADK LiteLlm (if running on localhost)
                  OR synthesizes Deterministic Fallback Explanation (if Ollama is offline)
        |
        v
[Typed AnalysisReport (domain/models.py)] ---> [Dashboard Tabs + JSON/Markdown Export]
```

### Supported Analysis Rules & Limitations
1. **`FIELD_REMOVED_BUT_CONSUMED` (`HIGH`)**: Triggered when a field present in `provider_before.json` is removed in `provider_after.json` and the consumer's Python AST reads that field. Upgraded from `INFERRED_RISK` to `REPRODUCED_FAILURE` only when the combined integration test executes and fails.
2. **`MISSING_REQUIRED_FIELD` (`HIGH`)**: Triggered when a field declared in `required_fields` (or JSON Schema `required`) is absent from `provider_after.json`.
3. **`FIELD_TYPE_CHANGED` (`HIGH` / `MEDIUM`)**: Triggered when a field's JSON value type changes incompatibly (e.g., `integer` $\rightarrow$ `string`) between before and after snapshots.
4. **Candidate Rename Heuristic**: Matches removed and added fields at the same nesting level when normalized identifiers match (`customer_id` $\leftrightarrow$ `customerId`) or sample values/types match. Always labeled as a candidate mapping.
5. **Compatibly Added Fields**: Optional new fields (e.g., `display_name`) that do not remove or alter existing consumed fields produce zero false critical/high alerts.

---

## Prerequisites

- **Python**: Python `3.10` or higher (`python --version`)
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
   MergeMesh runs with safe defaults out of the box. If you want to customize settings, copy `.env.example` to `.env`:
   ```bash
   # Windows PowerShell:
   Copy-Item .env.example .env

   # Linux / macOS:
   cp .env.example .env
   ```

4. **Optional — Pull Local Gemma Model via Ollama:**
   If you have Ollama installed and want local model explanations in addition to the deterministic engine:
   ```bash
   ollama pull gemma3:4b
   ollama serve
   ```
   *(If Ollama is not installed or stopped, MergeMesh automatically detects this and uses its deterministic explanation fallback without error.)*

---

## Configuration & Environment Variables

| Variable Name | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `MERGEMESH_AI_MODE` | No | `local` | `local` (Local Gemma via Ollama + ADK with deterministic fallback) or `deterministic` (Pure Python engine, zero LLM calls) |
| `OLLAMA_BASE_URL` | No | `http://localhost:11434` | Local loopback URL for Ollama (`localhost` / `127.0.0.1` enforced) |
| `OLLAMA_MODEL` | No | `gemma3:4b` | Local Ollama model tag used by Google ADK `LiteLlm` |
| `ALLOW_LOCAL_FALLBACK` | No | `true` | When `true`, preserves the full report and uses deterministic explanation fallback if Ollama is offline |
| `TEST_RUNNER_TIMEOUT_SECONDS` | No | `15` | Maximum timeout in seconds for each allow-listed `pytest` subprocess |
| `AI_REQUEST_TIMEOUT_SECONDS` | No | `25` | Maximum timeout in seconds for local Ollama inference requests |

---

## How to Run

From inside `agents/mergemesh` (with your virtual environment activated):

```bash
# Launch the interactive Streamlit engineering dashboard:
python -m streamlit run app.py
```

Or run the analysis engine directly from the command line without starting a browser:

```bash
python -c "from services.analyze_service import run_scenario_analysis; r = run_scenario_analysis('customer_id_rename', run_post_fix=True); print('Status:', r.overall_status, '| Finding:', r.finding_list[0].summary)"
```

---

## Example Input & Output

### Reproducing Scenario A — Cross-Repository Breaking Change (`customer_id_rename`)

1. Launch `python -m streamlit run app.py`.
2. In the sidebar, select **Scenario A — Breaking Field Rename (customer_id $\rightarrow$ customerId)** and click **Analyze Change Set**.
3. **Observed Input & Execution Flow:**
   - **PR A (`orders-api`)**: Renames response key `"customer_id"` to `"customerId"` in `repos/orders-api/provider.py`. Its isolated test (`test_provider_unit.py`) **PASSES** (`exit_code=0`).
   - **PR B (`checkout-client`)**: Reads `payload["customer_id"]` in `repos/checkout-client/client.py:6`. Its isolated test (`test_consumer_unit.py`) **PASSES** (`exit_code=0`) because its unit test uses a stale mock `{"customer_id": "cus_123", "status": "active"}`.
   - **Combined Integration Check (`test_combined_contract.py`)**: Calls `build_checkout_session(get_customer_response("cus_123"))` across both candidate versions and **FAILS** (`exit_code=1`) with `KeyError: 'customer_id'`.
4. **Validating the Fix (Post-Fix Pass):**
   - Click **Verify Suggested Fix (Post-Fix Pass)** in the sidebar or Overview tab.
   - MergeMesh executes `test_post_fix_contract.py` against the remediated consumer (`repos/checkout-client/client_fixed.py`), which **PASSES** (`exit_code=0`) and updates the verdict to `REMEDIATED_PASS`.

#### Example Output (Scenario A CLI / Report Summary)
```text
Overall Verdict: BREAKING_FAILURE_REPRODUCED
Tests Executed:
  - orders-api Isolated Unit Test (PR #142): PASS (exit_code=0)
  - checkout-client Isolated Unit Test (PR #89 — Stale Mock): PASS (exit_code=0, uses_stale_mock=True)
  - Combined Cross-Repo Integration Check: FAIL (exit_code=1, exception="KeyError: 'customer_id'")
Deterministic Finding:
  - [HIGH] missing_customer_id (FIELD_REMOVED_BUT_CONSUMED | verification_state=REPRODUCED_FAILURE)
  - Summary: Reproduced integration failure: orders-api removed 'customer_id' (renamed to 'customerId') while checkout-client still reads 'customer_id'.
  - Evidence: contracts/provider_before.json -> contracts/provider_after.json -> repos/checkout-client/client.py:6 -> repos/checkout-client/tests/test_combined_contract.py (exit_code=1)
Post-Fix Verification:
  - Post-Fix Combined Integration Check: PASS (exit_code=0) -> Overall Verdict: REMEDIATED_PASS
```

### Reproducing Scenario B — Safe Additive Change (`safe_optional_field`)

1. In the sidebar, select **Scenario B — Compatible Optional Field Addition (display_name)** (or click **Switch to Scenario B** on the Overview tab).
2. **Observed Outcome:**
   - `orders-api` adds optional field `"display_name": "Aditi Sharma"` while retaining `"customer_id"` and `"status"`.
   - Isolated provider test (`PASS`), isolated consumer test (`PASS`), and combined integration test (`PASS`) all exit with code `0`.
   - `0` breaking findings are reported (`Overall Verdict: COMPATIBLE_PASS`).

---

## How to Test

Run the full 27-test automated verification suite (unit tests, cross-repository integration flows, no-API-key fallback tests, and headless Streamlit `AppTest` UI verification) from `agents/mergemesh`:

```bash
python -m pytest -v
```

To run specific test suites:
```bash
# Unit tests only (contract diffing, AST usage, compatibility rules, scenario validation, config safety)
python -m pytest tests/unit -v

# Integration & Streamlit UI tests
python -m pytest tests/integration -v
```

---

## Known Limitations

- **Preloaded Synthetic PR Fixtures**: The prototype analyzes local repository fixtures under `fixtures/scenarios/` rather than fetching live GitHub pull requests over the network.
- **Static Python AST Scope**: Consumer field access detection inspects static string dictionary subscripts (`payload["key"]`) and `.get("key")` calls in Python files. Dynamic key construction (`payload[var]`), reflection, or non-Python consumers are not parsed in this version.
- **Top-Level & Nested Dict JSON Contracts**: Contract comparison targets JSON object payloads (`dict`) and does not yet parse full OpenAPI 3.1 or protobuf schemas.
- **Local Ollama Availability**: When Ollama is not running on `localhost:11434` or `gemma3:4b` has not been pulled, AI commentary automatically enters `FALLBACK` mode (`Completed with AI fallback`), using the deterministic rule engine for explanations.

---

## Security & Privacy Considerations

- **Zero Secrets & Zero External Data Transmission**: MergeMesh requires no API keys and never transmits source code, diffs, fixtures, test outputs, or prompts to external hosted AI services. `config/settings.py` enforces loopback-only URLs (`localhost` / `127.0.0.1` / `::1`) for Ollama checks.
- **Allow-Listed Subprocess Execution**: `execution/runner.py` only executes test IDs registered in `execution/fixture_registry.py` after verifying that resolved paths stay strictly within `fixtures/scenarios/<scenario_id>/`. It uses `shell=False`, strips environment secrets via `_build_sanitized_env()`, and enforces bounded execution timeouts.
- **Untrusted Diff Handling**: Diff files and source files are parsed as static data (`json`, `ast`), never passed to `eval()` or `exec()`.

---

## Future Improvements

- [ ] Add optional read-only GitHub PR diff and file fetcher with sandboxed container test execution.
- [ ] Support OpenAPI / JSON Schema specifications alongside sample response snapshots.
- [ ] Extend AST consumer scanners to TypeScript/JavaScript (`fetch` / `axios` response destructuring).

---

## License

*This project is contributed under the repository's open-source [MIT License](../../LICENSE).*
