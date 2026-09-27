"""
scripts/ai/synthesizer.py
=========================
AISynthesizer: On-Demand AI Intelligence synthesizer for code diffs and burst events.
Focused strictly on analyze_burst, diff parsing, and cache integration.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Any

try:
    from .rate_limiter import RateLimitManager
    from .cache_manager import AICacheManager
    from .prompt_builder import (
        is_sanitized_patch,
        detect_tech_stack,
        generate_directory_tree,
        chunk_diff,
        build_burst_prompt,
        build_file_diff_prompt,
    )
    from .provider_gemini import call_gemini, test_gemini_connection
    from .provider_ollama import call_ollama, test_ollama_connection
    from .changelog import generate_session_changelog, _fallback_changelog
    from .command_explainer import explain_command, _fallback_command_explanation
    from .config import (
        get_config_path,
        load_model_config,
        mask_model_config,
        save_model_config,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ai.rate_limiter import RateLimitManager
        from spd_analysis_engine.scripts.ai.cache_manager import AICacheManager
        from spd_analysis_engine.scripts.ai.prompt_builder import (
            is_sanitized_patch,
            detect_tech_stack,
            generate_directory_tree,
            chunk_diff,
            build_burst_prompt,
            build_file_diff_prompt,
        )
        from spd_analysis_engine.scripts.ai.provider_gemini import call_gemini, test_gemini_connection
        from spd_analysis_engine.scripts.ai.provider_ollama import call_ollama, test_ollama_connection
        from spd_analysis_engine.scripts.ai.changelog import generate_session_changelog, _fallback_changelog
        from spd_analysis_engine.scripts.ai.command_explainer import explain_command, _fallback_command_explanation
        from spd_analysis_engine.scripts.ai.config import (
            get_config_path,
            load_model_config,
            mask_model_config,
            save_model_config,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.ai.rate_limiter import RateLimitManager
        from scripts.ai.cache_manager import AICacheManager
        from scripts.ai.prompt_builder import (
            is_sanitized_patch,
            detect_tech_stack,
            generate_directory_tree,
            chunk_diff,
            build_burst_prompt,
            build_file_diff_prompt,
        )
        from scripts.ai.provider_gemini import call_gemini, test_gemini_connection
        from scripts.ai.provider_ollama import call_ollama, test_ollama_connection
        from scripts.ai.changelog import generate_session_changelog, _fallback_changelog
        from scripts.ai.command_explainer import explain_command, _fallback_command_explanation
        from scripts.ai.config import (
            get_config_path,
            load_model_config,
            mask_model_config,
            save_model_config,
        )


try:
    from .synthesizer_burst import BurstSynthesizerMixin
    from .synthesizer_file import FileSynthesizerMixin
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ai.synthesizer_burst import BurstSynthesizerMixin
        from spd_analysis_engine.scripts.ai.synthesizer_file import FileSynthesizerMixin
    except (ImportError, ModuleNotFoundError):
        from scripts.ai.synthesizer_burst import BurstSynthesizerMixin
        from scripts.ai.synthesizer_file import FileSynthesizerMixin

logger = logging.getLogger(__name__)


class AISynthesizer(BurstSynthesizerMixin, FileSynthesizerMixin):
    """
    On-Demand AI Intelligence synthesizer for code diffs and burst events.
    """

    @staticmethod
    def detect_tech_stack(workspace_path: Path | str | None) -> str:
        """Detect workspace tech stack from project indicator files."""
        return detect_tech_stack(workspace_path)

    @staticmethod
    def generate_directory_tree(workspace_path: Path | str | None, max_depth: int = 2) -> str:
        """Generate lightweight 2-level directory tree of the workspace, excluding ignore patterns."""
        return generate_directory_tree(workspace_path, max_depth=max_depth)

    rate_limiter: RateLimitManager = RateLimitManager(max_rpm=15)

    def __init__(self, engine_root: Path | str | None = None) -> None:
        if engine_root is None:
            self.engine_root = Path(__file__).resolve().parent.parent.parent
        else:
            self.engine_root = Path(engine_root).resolve()

        self.ai_data_dir = self.engine_root / "ai_data"
        self.config_path = self.ai_data_dir / "models_config.json"
        self.cache_dir = self.ai_data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_mgr = AICacheManager(self.cache_dir)

        self._config: dict[str, Any] = self.load_config(self.engine_root)

    @staticmethod
    def get_config_path(engine_root: Path | str | None = None) -> Path:
        """Return path to models_config.json."""
        return get_config_path(engine_root)

    @classmethod
    def load_config(cls, engine_root: Path | str | None = None) -> dict[str, Any]:
        """Load or reload model configuration adhering to strict credential precedence."""
        return load_model_config(engine_root)

    @classmethod
    def mask_config(cls, cfg: dict[str, Any]) -> dict[str, Any]:
        """Return config with sensitive Gemini API keys masked."""
        return mask_model_config(cfg)

    @classmethod
    def save_config(cls, config_dict: dict[str, Any], engine_root: Path | str | None = None) -> dict[str, Any]:
        """Save updated configuration to models_config.json."""
        return save_model_config(config_dict, engine_root)

    def reload_config(self) -> dict[str, Any]:
        """Reload configuration from disk or environment."""
        self._config = self.load_config(self.engine_root)
        return self._config

    @classmethod
    def test_ollama_connection(
        cls,
        base_url: str | None = None,
        model: str | None = None,
        engine_root: Path | str | None = None,
    ) -> dict[str, Any]:
        """Test connection to an Ollama instance by querying /api/tags."""
        return test_ollama_connection(base_url=base_url, model=model)

    @classmethod
    def test_gemini_connection(
        cls,
        api_key: str | None = None,
        key_id: str | None = None,
        model: str | None = None,
        engine_root: Path | str | None = None,
    ) -> dict[str, Any]:
        """Test Gemini API connection by querying the models list endpoint."""
        cfg = cls.load_config(engine_root)
        return test_gemini_connection(api_key=api_key, key_id=key_id, model=model, config=cfg)

    def compute_diff_hash(self, diff_text: str) -> str:
        """Compute a deterministic SHA-256 hash of normalized diff content."""
        normalized = diff_text.strip().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def get_cached_analysis(
        self,
        diff_hash: str,
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Check if an analysis for this diff hash already exists in disk cache."""
        return self.cache_mgr.get(
            diff_hash=diff_hash,
            project_name=project_name,
            session_id=session_id,
            burst_id=burst_id,
        )

    def save_cached_analysis(
        self,
        diff_hash: str,
        analysis: dict[str, Any],
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> None:
        """Persist analysis result to disk cache."""
        self.cache_mgr.save(
            diff_hash=diff_hash,
            analysis=analysis,
            project_name=project_name,
            session_id=session_id,
            burst_id=burst_id,
        )

    def chunk_diff(self, diff_text: str, max_lines: int | None = None, max_chars: int = 30000) -> str:
        """Chunk/truncate diffs exceeding max_lines or max_chars."""
        if max_lines is None:
            max_lines = self._config.get("analysis", {}).get("max_diff_lines_for_summary", 250)
        return chunk_diff(diff_text, max_lines=max_lines, max_chars=max_chars)

    def _build_prompt(
        self,
        diff_text: str,
        first_file: str | None = None,
        summary: str | None = None,
        files: list[str] | None = None,
        project_name: str | None = None,
        workspace_path: Path | str | None = None,
        tech_stack: str | None = None,
        dir_tree: str | None = None,
        previous_summary: str | dict[str, Any] | None = None,
        executed_commands: list[str] | None = None,
    ) -> str:
        return build_burst_prompt(
            diff_text=diff_text,
            first_file=first_file,
            summary=summary,
            files=files,
            project_name=project_name,
            workspace_path=workspace_path,
            tech_stack=tech_stack,
            dir_tree=dir_tree,
            previous_summary=previous_summary,
            executed_commands=executed_commands,
        )

    def _parse_llm_json(self, raw_text: str) -> dict[str, Any]:
        """Strip markdown fences and parse structured JSON from LLM output."""
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            import re
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        import re, json
        json_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(1)

        data = json.loads(cleaned)
        intent = str(data.get("intent") or data.get("commit_title") or "Code edit burst analyzed.")
        arch_impact = str(data.get("architecture_impact") or "Component structure and data flow maintained.")
        exec_rationale = str(data.get("execution_rationale") or data.get("rationale") or "Terminal commands executed during validation and runtime verification.")

        mods_raw = data.get("key_modifications") or data.get("summary") or data.get("commit_body") or []
        if isinstance(mods_raw, str):
            mods = [s.strip("- ") for s in mods_raw.splitlines() if s.strip()] or [mods_raw]
        elif isinstance(mods_raw, list):
            mods = [str(x) for x in mods_raw]
        else:
            mods = ["Code changes applied."]

        gains = str(data.get("functionality_gained", "Enhanced functionality."))

        result = dict(data)
        result["intent"] = intent
        result["execution_rationale"] = exec_rationale
        result["architecture_impact"] = arch_impact
        result["key_modifications"] = mods
        result["summary"] = mods
        result["functionality_gained"] = gains
        return result

    def _call_ollama(self, prompt: str) -> dict[str, Any]:
        """Issue HTTP POST to local Ollama instance."""
        if self.config_path.exists():
            self._config = self.load_config(self.engine_root)
        ollama_cfg = self._config.get("ollama", {})
        return call_ollama(prompt, ollama_cfg, self._parse_llm_json)

    def _call_gemini(self, prompt: str) -> dict[str, Any]:
        """Issue API call to Gemini using official Google GenAI SDK or HTTPS fallback."""
        if self.config_path.exists():
            self._config = self.load_config(self.engine_root)
        gemini_cfg = self._config.get("gemini", {})
        return call_gemini(prompt, gemini_cfg, self.rate_limiter, self._parse_llm_json)

    def _fallback_analysis(
        self,
        files: list[str],
        first_file: str | None,
        summary: str | None,
        reason: str = "offline",
        project_name: str | None = None,
        workspace_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Generate a meaningful heuristic fallback analysis when AI is unreachable."""
        f_count = len(files)
        entry_txt = f" starting at '{first_file}'" if first_file else ""
        desc = summary or f"Burst modification affecting {f_count} file(s)"

        summary_bullets = [
            f"Atomic file modification detected across {f_count} file(s){entry_txt}.",
            f"Event details: {desc}",
        ]
        if files:
            summary_bullets.append(f"Target files: {', '.join(files[:6])}{' ...' if len(files) > 6 else ''}")

        return {
            "intent": f"Code modification bundle ({desc}). [AI offline fallback]",
            "execution_rationale": "Terminal commands executed during development workflow to verify changes in workspace context.",
            "architecture_impact": f"Component modifications committed to project '{project_name or 'workspace'}' without regression.",
            "key_modifications": summary_bullets,
            "summary": summary_bullets,
            "functionality_gained": "Modifications captured into ACID SQLite WAL storage, unified diff cache, and isolated Shadow Git micro-versioning.",
            "provider": "offline-fallback",
            "fallback_reason": reason,
            "cached": False,
        }


    def generate_session_changelog(
        self,
        session_events: list[dict[str, Any]],
        diffs: list[dict[str, Any]] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Delegate session changelog generation to scripts.ai.changelog."""
        return generate_session_changelog(session_events, diffs=diffs, provider=provider, synthesizer=self)

    def _fallback_changelog(
        self,
        files: list[str],
        events: list[dict[str, Any]],
        reason: str = "offline",
    ) -> dict[str, Any]:
        """Delegate fallback changelog generation to scripts.ai.changelog."""
        return _fallback_changelog(files=files, events=events, reason=reason)

    def explain_command(
        self,
        project_name: str,
        command_str: str,
        exit_code: int = 0,
        duration_s: float = 0.0,
        surrounding_burst_context: list[dict[str, Any]] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Delegate command explanation to scripts.ai.command_explainer."""
        return explain_command(
            project_name=project_name,
            command_str=command_str,
            exit_code=exit_code,
            duration_s=duration_s,
            surrounding_burst_context=surrounding_burst_context,
            provider=provider,
            synthesizer=self,
        )

    def _fallback_command_explanation(
        self,
        command_str: str,
        exit_code: int = 0,
        duration_s: float = 0.0,
        reason: str = "offline",
    ) -> dict[str, Any]:
        """Delegate fallback command explanation to scripts.ai.command_explainer."""
        return _fallback_command_explanation(command_str, exit_code=exit_code, duration_s=duration_s, reason=reason)


__all__ = ["AISynthesizer"]
