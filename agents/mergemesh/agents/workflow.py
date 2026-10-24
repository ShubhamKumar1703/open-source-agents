"""Local-first ADK execution workflow with schema validation and deterministic fallback."""

from __future__ import annotations

import asyncio
import json
import re
import time

from agents.factory import create_adk_agent
from agents.prompts import build_agent_context_prompt
from agents.schemas import AIExplanationSchema
from config.settings import AppSettings, check_provider_readiness
from domain.enums import AIStatus
from domain.models import (
    AIExplanation,
    ConsumerFieldAccess,
    ContractComparison,
    Finding,
    ScenarioDefinition,
    TestExecutionResult,
)


def build_deterministic_explanation(
    scenario: ScenarioDefinition,
    comparison: ContractComparison,
    consumer_accesses: list[ConsumerFieldAccess],
    findings: list[Finding],
    test_results: list[TestExecutionResult],
    status: AIStatus = "FALLBACK",
    provider_label: str = "Deterministic Rule Engine (Fallback)",
    model_id: str | None = None,
    fallback_reason: str | None = None,
    duration_ms: int = 0,
) -> AIExplanation:
    """Construct a ground-truth explanation from measured deterministic evidence."""
    combined_test = next((t for t in test_results if t.test_kind == "combined_integration"), None)
    provider_test = next((t for t in test_results if t.test_kind == "provider_isolated"), None)
    consumer_test = next((t for t in test_results if t.test_kind == "consumer_isolated"), None)

    if findings:
        f0 = findings[0]
        field_name = f0.field or "unknown_field"
        replacement = f0.replacement_candidate

        if f0.verification_state == "REPRODUCED_FAILURE" and combined_test:
            summary = (
                f"Reproduced cross-repository contract failure: `{scenario.provider_repo}` removed "
                f"`{field_name}`"
                + (f" (renamed to `{replacement}`)" if replacement else "")
                + f", causing `{scenario.consumer_repo}` to fail at runtime with `{combined_test.failure_summary}`."
            )
        else:
            summary = f0.summary

        why_it_matters = (
            f"Isolated CI checks in both `{scenario.provider_repo}` and `{scenario.consumer_repo}` passed "
            f"because `{scenario.consumer_repo}`'s unit test relies on a stale mock payload that still supplies "
            f"`{field_name}`. Deploying these two candidate versions together breaks checkout session creation."
        )

        observed_facts: list[str] = [
            f"Provider before contract (`{comparison.provider_before_path}`) contained `{field_name}`.",
            f"Provider after contract (`{comparison.provider_after_path}`) removed `{field_name}`"
            + (f" and added `{replacement}`." if replacement else "."),
        ]
        for acc in consumer_accesses:
            if acc.field_path == field_name:
                observed_facts.append(
                    f"Consumer source `{acc.file_path}:{acc.line}` reads `{acc.field_path}` via `{acc.access_mode}` (`{acc.code_snippet}`)."
                )
        if provider_test and consumer_test:
            observed_facts.append(
                f"Isolated tests passed: `{provider_test.file_path}` ({provider_test.status}) and `{consumer_test.file_path}` ({consumer_test.status}, using mock)."
            )
        if combined_test:
            observed_facts.append(
                f"Combined test `{combined_test.file_path}` exited with code {combined_test.exit_code} ({combined_test.status}): `{combined_test.failure_summary}`."
            )

        hypotheses: list[str] = []
        if replacement:
            hypotheses.append(
                f"Candidate field rename `{field_name}` -> `{replacement}` inferred from matching normalized key name and sample value (`cus_123`)."
            )

        fix_options = [
            f"Option 1 (Consumer update - Recommended for forward migration): Update `{scenario.consumer_source}` in `{scenario.consumer_repo}` to read `payload.get(\"{replacement}\") or payload[\"{field_name}\"]` and update the stale unit test mock.",
            f"Option 2 (Provider backward-compatible dual-write): Update `{scenario.provider_source}` in `{scenario.provider_repo}` to emit both `{replacement}` and deprecated `{field_name}` until all downstream consumers migrate.",
        ]
        suggested_test = (
            "def test_contract_compatibility():\n"
            "    payload = get_customer_response('cus_123')\n"
            "    session = build_checkout_session(payload)\n"
            "    assert session['session_customer'] == 'cus_123'"
        )
    else:
        summary = (
            f"No breaking contract incompatibility detected between `{scenario.provider_repo}` and `{scenario.consumer_repo}`. "
            f"Added fields ({', '.join(comparison.added_fields) or 'none'}) are backward-compatible and the combined integration check passed."
        )
        why_it_matters = (
            f"`{scenario.provider_repo}` preserved all fields consumed by `{scenario.consumer_repo}` "
            f"({', '.join(a.field_path for a in consumer_accesses) or 'none'}). Candidate changes can be merged safely with respect to this interface."
        )
        observed_facts = [
            f"Provider contract retained required fields: {', '.join(sorted(comparison.before_fields.keys()))}.",
            f"Added fields in `{comparison.provider_after_path}`: {', '.join(comparison.added_fields) or 'none'}.",
        ]
        if combined_test:
            observed_facts.append(
                f"Combined integration test `{combined_test.file_path}` executed and exited with status {combined_test.status} (exit code {combined_test.exit_code})."
            )
        hypotheses = [
            "Downstream consumers ignoring unknown JSON fields will remain unaffected by the optional addition."
        ]
        fix_options = [
            "No code remediation needed. Optionally update consumer mock fixtures to include the newly added optional field for completeness."
        ]
        suggested_test = None

    limitations = [
        "Analysis is scoped to the loaded local REST/JSON scenario fixture.",
        "Consumer AST analysis inspects static string dictionary accesses in Python source files.",
        "Deterministic analyzer and executed pytest runner own the pass/fail verdict.",
    ]

    return AIExplanation(
        status=status,
        provider=provider_label,
        model=model_id,
        summary=summary,
        why_it_matters=why_it_matters,
        observed_facts=observed_facts,
        hypotheses=hypotheses,
        suggested_fix_options=fix_options,
        suggested_test=suggested_test,
        confidence_label="high",
        limitations=limitations,
        duration_ms=duration_ms,
        fallback_reason=fallback_reason,
    )


def _extract_json_block(raw_text: str) -> dict:
    """Extract and parse a JSON object from model text response."""
    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        brace_start = cleaned.find("{")
        brace_end = cleaned.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            cleaned = cleaned[brace_start : brace_end + 1]
    return json.loads(cleaned)


async def _run_adk_agent_async(agent: object, prompt_text: str) -> str:
    """Execute the Google ADK Agent in-memory against local Ollama and collect text response."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent=agent, app_name="mergemesh")  # type: ignore[arg-type]
    session = await runner.session_service.create_session(
        app_name="mergemesh",
        user_id="mergemesh_evaluator",
    )
    content = types.Content(role="user", parts=[types.Part.from_text(text=prompt_text)])
    collected_parts: list[str] = []
    async for event in runner.run_async(
        user_id="mergemesh_evaluator",
        session_id=session.id,
        new_message=content,
    ):
        if getattr(event, "content", None) and getattr(event.content, "parts", None):
            for part in event.content.parts:
                if getattr(part, "text", None):
                    collected_parts.append(part.text)
    return "\n".join(collected_parts).strip()


def _invoke_adk_with_timeout(agent: object, prompt_text: str, timeout_sec: int) -> str:
    """Run the async ADK runner within a bounded timeout."""
    return asyncio.run(
        asyncio.wait_for(_run_adk_agent_async(agent, prompt_text), timeout=float(timeout_sec))
    )


def generate_ai_explanation(
    settings: AppSettings,
    scenario: ScenarioDefinition,
    comparison: ContractComparison,
    consumer_accesses: list[ConsumerFieldAccess],
    findings: list[Finding],
    test_results: list[TestExecutionResult],
) -> AIExplanation:
    """Run the local ADK model workflow with strict validation and safe deterministic fallback."""
    start_ns = time.perf_counter_ns()

    if settings.ai_mode == "deterministic":
        elapsed = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        return build_deterministic_explanation(
            scenario=scenario,
            comparison=comparison,
            consumer_accesses=consumer_accesses,
            findings=findings,
            test_results=test_results,
            status="SKIPPED",
            provider_label="Deterministic Local Engine (No LLM)",
            model_id="deterministic",
            fallback_reason="Pure deterministic mode selected; no LLM called.",
            duration_ms=elapsed,
        )

    readiness = check_provider_readiness(settings, timeout_sec=1.5)
    descriptor = create_adk_agent(settings, stage="primary")

    def _handle_unavailable(reason: str) -> AIExplanation:
        elapsed = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        if settings.allow_local_fallback:
            return build_deterministic_explanation(
                scenario=scenario,
                comparison=comparison,
                consumer_accesses=consumer_accesses,
                findings=findings,
                test_results=test_results,
                status="FALLBACK",
                provider_label=f"{descriptor.provider_label} -> Deterministic Fallback",
                model_id=descriptor.model_id,
                fallback_reason=reason,
                duration_ms=elapsed,
            )
        return build_deterministic_explanation(
            scenario=scenario,
            comparison=comparison,
            consumer_accesses=consumer_accesses,
            findings=findings,
            test_results=test_results,
            status="UNAVAILABLE",
            provider_label=descriptor.provider_label,
            model_id=descriptor.model_id,
            fallback_reason=reason,
            duration_ms=elapsed,
        )

    if settings.ai_mode == "local":
        if not readiness.ollama_reachable:
            return _handle_unavailable(readiness.ollama_status_message)
        if not readiness.ollama_model_installed:
            return _handle_unavailable(readiness.ollama_status_message)
    elif settings.ai_mode == "gemini":
        if not readiness.gemini_configured:
            return _handle_unavailable(readiness.gemini_status_message)
        return _handle_unavailable(
            descriptor.error_reason
            or "External hosted LLM calls are disabled in local-only runtime mode."
        )
    elif settings.ai_mode == "hybrid":
        if not (readiness.ollama_reachable and readiness.ollama_model_installed):
            return _handle_unavailable(
                f"Local Ollama ({readiness.ollama_status_message}) is not ready, and external hosted calls are disabled."
            )
        descriptor = create_adk_agent(
            settings=AppSettings(**{**settings.__dict__, "ai_mode": "local"}),
            stage="primary",
        )

    if descriptor.adk_agent is None:
        return _handle_unavailable(descriptor.error_reason or "Local ADK agent could not be initialized.")

    prompt_text = build_agent_context_prompt(
        scenario=scenario,
        comparison=comparison,
        consumer_accesses=consumer_accesses,
        findings=findings,
        test_results=test_results,
    )

    try:
        raw_output = _invoke_adk_with_timeout(
            agent=descriptor.adk_agent,
            prompt_text=prompt_text,
            timeout_sec=settings.ai_request_timeout_seconds,
        )
        parsed_dict = _extract_json_block(raw_output)
        validated = AIExplanationSchema.model_validate(parsed_dict)
        elapsed = int((time.perf_counter_ns() - start_ns) / 1_000_000)

        return AIExplanation(
            status="SUCCESS",
            provider=descriptor.provider_label,
            model=descriptor.model_id,
            summary=validated.summary,
            why_it_matters=validated.why_it_matters,
            observed_facts=validated.observed_facts,
            hypotheses=validated.hypotheses,
            suggested_fix_options=validated.suggested_fix_options,
            suggested_test=validated.suggested_test,
            confidence_label=validated.confidence_label,
            limitations=validated.limitations
            or ["Deterministic analyzer and executed test runner retain authority over the verdict."],
            duration_ms=elapsed,
            fallback_reason=None,
        )
    except Exception as exc:
        return _handle_unavailable(f"Local AI invocation or schema validation failed: {type(exc).__name__}: {exc}")
