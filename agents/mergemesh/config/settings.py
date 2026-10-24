"""Environment-driven settings and local runtime readiness checks for MergeMesh.

Designed for local-first operation with zero external API credit requirements.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

AIMode = Literal["local", "deterministic", "gemini", "hybrid"]
GeminiBackend = Literal["developer_api", "vertex_ai"]

VALID_AI_MODES: set[str] = {"local", "deterministic", "gemini", "hybrid"}
VALID_GEMINI_BACKENDS: set[str] = {"developer_api", "vertex_ai"}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = PROJECT_ROOT / "fixtures" / "scenarios"
REPORTS_ROOT = PROJECT_ROOT / "reports"


def _is_loopback_url(url: str) -> bool:
    """Ensure Ollama checks only ever contact loopback/localhost addresses."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1"}
    except Exception:
        return False


@dataclass(frozen=True)
class ProviderReadiness:
    ollama_reachable: bool
    ollama_model_installed: bool
    ollama_available_models: list[str]
    ollama_status_message: str
    gemini_configured: bool
    gemini_backend: str
    gemini_status_message: str


@dataclass(frozen=True)
class AppSettings:
    ai_mode: AIMode
    ollama_base_url: str
    ollama_model: str
    gemini_model: str
    gemini_backend: GeminiBackend
    use_vertex_ai: bool
    google_cloud_project: str
    google_cloud_location: str
    has_gemini_api_key: bool
    allow_local_fallback: bool
    ai_request_timeout_seconds: int
    ai_max_output_tokens: int
    ai_max_calls_per_analysis: int
    test_runner_timeout_seconds: int
    fixtures_root: Path = FIXTURES_ROOT
    reports_root: Path = REPORTS_ROOT

    def get_active_gemini_key(self) -> str | None:
        """Return API key for optional config inspection only; never log or display."""
        google_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if google_key:
            return google_key
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if gemini_key:
            return gemini_key
        return None

    def is_gemini_configured(self) -> bool:
        if self.use_vertex_ai or self.gemini_backend == "vertex_ai":
            return bool(self.google_cloud_project and self.google_cloud_location)
        return self.has_gemini_api_key


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, default: int, minimum: int = 1) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        parsed = int(value.strip())
        return max(parsed, minimum)
    except ValueError:
        return default


def load_settings(override_ai_mode: str | None = None) -> AppSettings:
    """Load and validate application settings from environment variables."""
    raw_mode = (override_ai_mode or os.environ.get("MERGEMESH_AI_MODE", "local")).strip().lower()
    if raw_mode not in VALID_AI_MODES:
        raise ValueError(
            f"Invalid MERGEMESH_AI_MODE '{raw_mode}'. Expected one of: {sorted(VALID_AI_MODES)}"
        )

    raw_backend = os.environ.get("GEMINI_BACKEND", "developer_api").strip().lower()
    if raw_backend not in VALID_GEMINI_BACKENDS:
        raw_backend = "developer_api"

    use_vertex = _parse_bool(os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"), default=(raw_backend == "vertex_ai"))
    if use_vertex:
        raw_backend = "vertex_ai"

    google_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    has_key = bool(google_key or gemini_key)

    gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
    if not gemini_model or gemini_model == "SET_A_SUPPORTED_MODEL_ID":
        gemini_model = "gemini-2.5-flash"

    return AppSettings(
        ai_mode=raw_mode,  # type: ignore[arg-type]
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "gemma3:4b").strip(),
        gemini_model=gemini_model,
        gemini_backend=raw_backend,  # type: ignore[arg-type]
        use_vertex_ai=use_vertex,
        google_cloud_project=os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip(),
        google_cloud_location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1").strip(),
        has_gemini_api_key=has_key,
        allow_local_fallback=_parse_bool(os.environ.get("ALLOW_LOCAL_FALLBACK"), default=True),
        ai_request_timeout_seconds=_parse_int(os.environ.get("AI_REQUEST_TIMEOUT_SECONDS"), default=25),
        ai_max_output_tokens=_parse_int(os.environ.get("AI_MAX_OUTPUT_TOKENS"), default=1200),
        ai_max_calls_per_analysis=_parse_int(os.environ.get("AI_MAX_CALLS_PER_ANALYSIS"), default=2),
        test_runner_timeout_seconds=_parse_int(os.environ.get("TEST_RUNNER_TIMEOUT_SECONDS"), default=15),
    )


def check_provider_readiness(settings: AppSettings, timeout_sec: float = 1.5) -> ProviderReadiness:
    """Check whether local Ollama is reachable without making any external network requests."""
    ollama_reachable = False
    ollama_model_installed = False
    available_models: list[str] = []
    ollama_msg = f"Ollama not reachable at {settings.ollama_base_url}"

    if not _is_loopback_url(settings.ollama_base_url):
        ollama_msg = (
            f"Blocked non-local OLLAMA_BASE_URL ({settings.ollama_base_url}); "
            "MergeMesh enforces local-only inference."
        )
    else:
        try:
            resp = requests.get(f"{settings.ollama_base_url}/api/tags", timeout=timeout_sec)
            if resp.status_code == 200:
                ollama_reachable = True
                data = resp.json()
                models_list = data.get("models", [])
                available_models = [str(m.get("name", "")) for m in models_list if m.get("name")]
                target = settings.ollama_model
                ollama_model_installed = any(
                    m == target or m.startswith(f"{target}:") or target in m
                    for m in available_models
                )
                if ollama_model_installed:
                    ollama_msg = f"Ollama ready ({settings.ollama_model})"
                else:
                    ollama_msg = (
                        f"Ollama running, but model '{settings.ollama_model}' is not pulled "
                        f"(found: {', '.join(available_models) if available_models else 'none'})"
                    )
            else:
                ollama_msg = f"Ollama returned HTTP {resp.status_code}"
        except Exception:
            ollama_msg = f"Ollama service offline ({settings.ollama_base_url}) — deterministic fallback active"

    gemini_ready = settings.is_gemini_configured()
    if settings.gemini_backend == "vertex_ai":
        if gemini_ready:
            gemini_msg = (
                "Optional Vertex AI config present (disabled in local-only runtime; "
                "no external API calls are made)"
            )
        else:
            gemini_msg = "Vertex AI not configured (GOOGLE_CLOUD_PROJECT missing; disabled in local-only runtime)"
    else:
        if gemini_ready:
            gemini_msg = (
                "Optional Gemini API key present (disabled in local-only runtime; "
                "no external API calls are made)"
            )
        else:
            gemini_msg = "Gemini API key not configured (disabled in local-only runtime; zero API credits required)"

    return ProviderReadiness(
        ollama_reachable=ollama_reachable,
        ollama_model_installed=ollama_model_installed,
        ollama_available_models=available_models,
        ollama_status_message=ollama_msg,
        gemini_configured=gemini_ready,
        gemini_backend=settings.gemini_backend,
        gemini_status_message=gemini_msg,
    )
