"""
scripts/ai package
==================
Modular AI intelligence layer for the SPD Analysis Engine.
"""

from .rate_limiter import RateLimitManager
from .cache_manager import AICacheManager
from .cache_queries import CacheQueriesMixin
from .prompt_builder import (
    is_sanitized_patch,
    detect_tech_stack,
    generate_directory_tree,
    chunk_diff,
    build_burst_prompt,
    build_command_prompt,
    build_file_diff_prompt,
    SANITIZATION_IGNORE_PATTERNS,
)
from .provider_gemini import call_gemini, test_gemini_connection
from .provider_ollama import call_ollama, test_ollama_connection
from .changelog import (
    generate_session_changelog,
    _fallback_changelog,
    ChangelogMixin,
)
from .command_explainer import (
    explain_command,
    _fallback_command_explanation,
    CommandExplainerMixin,
)
from .config import (
    get_config_path,
    load_model_config,
    mask_model_config,
    save_model_config,
)
from .synthesizer import AISynthesizer
from .synthesizer_burst import BurstSynthesizerMixin
from .synthesizer_file import FileSynthesizerMixin

__all__ = [
    "RateLimitManager",
    "AICacheManager",
    "is_sanitized_patch",
    "detect_tech_stack",
    "generate_directory_tree",
    "chunk_diff",
    "build_burst_prompt",
    "build_command_prompt",
    "SANITIZATION_IGNORE_PATTERNS",
    "call_gemini",
    "test_gemini_connection",
    "call_ollama",
    "test_ollama_connection",
    "generate_session_changelog",
    "_fallback_changelog",
    "explain_command",
    "_fallback_command_explanation",
    "ChangelogMixin",
    "CommandExplainerMixin",
    "get_config_path",
    "load_model_config",
    "mask_model_config",
    "save_model_config",
    "AISynthesizer",
]
