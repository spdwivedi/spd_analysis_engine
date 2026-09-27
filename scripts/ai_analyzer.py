"""scripts/ai_analyzer.py - Lightweight Facade re-exporting from scripts.ai.*"""
from __future__ import annotations

from .ai.rate_limiter import RateLimitManager
from .ai.cache_manager import AICacheManager
from .ai.prompt_builder import (
    is_sanitized_patch,
    detect_tech_stack,
    generate_directory_tree,
    chunk_diff,
    build_file_diff_prompt,
    SANITIZATION_IGNORE_PATTERNS,
)
from .ai.provider_gemini import call_gemini, test_gemini_connection
from .ai.provider_ollama import call_ollama, test_ollama_connection
from .ai.changelog import generate_session_changelog, _fallback_changelog
from .ai.command_explainer import explain_command, _fallback_command_explanation
from .ai.synthesizer import AISynthesizer

__all__ = [
    "RateLimitManager",
    "AICacheManager",
    "is_sanitized_patch",
    "detect_tech_stack",
    "generate_directory_tree",
    "chunk_diff",
    "build_file_diff_prompt",
    "SANITIZATION_IGNORE_PATTERNS",
    "call_gemini",
    "test_gemini_connection",
    "call_ollama",
    "test_ollama_connection",
    "generate_session_changelog",
    "_fallback_changelog",
    "explain_command",
    "_fallback_command_explanation",
    "AISynthesizer",
]
