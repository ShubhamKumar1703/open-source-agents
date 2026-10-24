"""Unit tests for configuration validation, local-only runtime enforcement, and AI fallback safety."""

from __future__ import annotations

import pytest

from agents.schemas import AIExplanationSchema
from agents.workflow import _extract_json_block
from config.settings import check_provider_readiness, load_settings


def test_default_ai_mode_is_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MERGEMESH_AI_MODE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    settings = load_settings()
    assert settings.ai_mode == "local"
    assert settings.is_gemini_configured() is False


def test_deterministic_ai_mode_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERGEMESH_AI_MODE", "deterministic")
    settings = load_settings()
    assert settings.ai_mode == "deterministic"


def test_non_loopback_ollama_url_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://external.example.com:11434")
    settings = load_settings()
    readiness = check_provider_readiness(settings)
    assert readiness.ollama_reachable is False
    assert "Blocked non-local OLLAMA_BASE_URL" in readiness.ollama_status_message


def test_invalid_ai_mode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERGEMESH_AI_MODE", "unsupported_provider")
    with pytest.raises(ValueError, match="Invalid MERGEMESH_AI_MODE"):
        load_settings()


def test_gemini_mode_and_vertex_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERGEMESH_AI_MODE", "gemini")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-gcp-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    settings = load_settings()

    assert settings.ai_mode == "gemini"
    assert settings.gemini_backend == "vertex_ai"
    assert settings.is_gemini_configured() is True


def test_malformed_ai_json_rejected_by_schema() -> None:
    with pytest.raises(Exception):
        bad_dict = _extract_json_block("not valid json at all")
        AIExplanationSchema.model_validate(bad_dict)
