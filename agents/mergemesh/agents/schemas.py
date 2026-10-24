"""Pydantic schemas for validating structured AI explanations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AIExplanationSchema(BaseModel):
    """Validated structured output expected from the MergeMesh ADK explainer agent."""

    summary: str = Field(..., min_length=10, description="Brief developer-friendly summary of the compatibility outcome.")
    why_it_matters: str = Field(..., min_length=10, description="Downstream runtime impact explanation.")
    observed_facts: list[str] = Field(
        default_factory=list,
        description="Facts directly backed by deterministic contract analysis and test execution.",
    )
    hypotheses: list[str] = Field(
        default_factory=list,
        description="Inferred risks or implicit contract hypotheses clearly separated from facts.",
    )
    suggested_fix_options: list[str] = Field(
        default_factory=list,
        min_length=1,
        description="Concrete remediation options for provider or consumer owners to review.",
    )
    suggested_test: str | None = Field(
        default=None,
        description="Suggested targeted integration assertion or test snippet (as text only).",
    )
    confidence_label: Literal["high", "medium", "low"] = Field(
        default="high",
        description="Confidence level in the explanation.",
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="Explicit caveats and scope boundaries.",
    )
