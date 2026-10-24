"""ADK Agent factory configured for local-first execution via Ollama (Gemma).

Hosted external LLM providers are isolated and disabled in the default local-only runtime
so that no project data leaves the machine and no paid API credits are required.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

from agents.prompts import SYSTEM_PROMPT
from agents.tools import ADK_DETERMINISTIC_TOOLS
from config.settings import AppSettings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="google.adk")


@dataclass
class AgentDescriptor:
    mode: str
    provider_label: str
    model_id: str
    adk_agent: Any | None
    error_reason: str | None = None


def create_adk_agent(settings: AppSettings, stage: str = "primary") -> AgentDescriptor:
    """Create a Google ADK Agent configured for local Ollama inference (`ollama_chat/<model>`)."""
    effective_mode = settings.ai_mode
    if settings.ai_mode == "hybrid":
        effective_mode = "local" if stage == "extract" else "gemini"

    if effective_mode == "deterministic":
        return AgentDescriptor(
            mode="deterministic",
            provider_label="Deterministic Local Engine (No LLM)",
            model_id="deterministic",
            adk_agent=None,
            error_reason="Pure deterministic mode selected; LLM calls skipped by design.",
        )

    # Optional hosted modes are separated and disabled from making external network calls
    if effective_mode == "gemini":
        model_id = settings.gemini_model
        provider_label = (
            "Hosted Gemini (Optional Integration — Disabled in Local-Only Runtime)"
            if settings.is_gemini_configured()
            else "Hosted Gemini (Not Configured)"
        )
        reason = (
            "External hosted API calls are disabled in local-only runtime mode to ensure zero API credit dependency and local data privacy."
            if settings.is_gemini_configured()
            else "Gemini credentials are not configured. MergeMesh runs in local-only runtime mode without external API keys."
        )
        return AgentDescriptor(
            mode="gemini",
            provider_label=provider_label,
            model_id=model_id,
            adk_agent=None,
            error_reason=reason,
        )

    # Local mode via Google ADK + LiteLLM + Ollama
    model_tag = f"ollama_chat/{settings.ollama_model}"
    try:
        from google.adk.agents import Agent
        from google.adk.models.lite_llm import LiteLlm

        llm_model = LiteLlm(model=model_tag)
        agent = Agent(
            name="mergemesh_local_gemma_explainer",
            model=llm_model,
            instruction=SYSTEM_PROMPT,
            description="Explains cross-repository compatibility evidence using local Gemma via Ollama.",
            tools=ADK_DETERMINISTIC_TOOLS,
        )
        return AgentDescriptor(
            mode="local",
            provider_label="Local Gemma (ADK + Ollama)",
            model_id=model_tag,
            adk_agent=agent,
        )
    except Exception as exc:
        return AgentDescriptor(
            mode="local",
            provider_label="Local Gemma (ADK + Ollama)",
            model_id=model_tag,
            adk_agent=None,
            error_reason=f"Failed to initialize local ADK LiteLlm model '{model_tag}': {exc}",
        )
