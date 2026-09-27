"""
scripts/ai/synthesizer_file.py
===============================
FileSynthesizerMixin: _fallback_file_analysis and analyze_file methods
extracted from AISynthesizer to keep individual modules under 500 lines.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

try:
    from .prompt_builder import build_file_diff_prompt
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ai.prompt_builder import build_file_diff_prompt
    except (ImportError, ModuleNotFoundError):
        from scripts.ai.prompt_builder import build_file_diff_prompt

logger = logging.getLogger(__name__)


class FileSynthesizerMixin:
    """
    Mixin providing per-file AI analysis methods for AISynthesizer.
    Requires self.cache_mgr, self.rate_limiter, self.compute_diff_hash,
    self.chunk_diff, self._call_gemini, self._call_ollama, self.load_config
    from the base class.
    """

    def _fallback_file_analysis(
        self,
        file_path: str,
        diff_text: str,
        mode: str = "deep",
        reason: str = "offline",
    ) -> dict[str, Any]:
        """Generate heuristic fallback analysis for a single file diff."""
        added = 0
        removed = 0
        symbols: list[str] = []
        for line in diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
                stripped = line[1:].strip()
                if stripped.startswith(("def ", "class ", "async def ", "function ", "export function ", "export class ", "const ", "let ")):
                    parts = stripped.split("(")
                    if parts:
                        sym = parts[0].strip()
                        if sym not in symbols:
                            symbols.append(sym)
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1

        total_lines = added + removed
        risk = "LOW"
        if total_lines > 150:
            risk = "MEDIUM"
        if total_lines > 400 or any(k in diff_text.upper() for k in ("DROP ", "DELETE ", "ALTER ", "DEPRECATED", "BREAKING")):
            risk = "HIGH"

        why = (
            f"Code updates affecting {total_lines} lines (+{added}/-{removed})."
            if total_lines > 0
            else "File touched with minor or formatting modifications."
        )

        clean_mode = "deep" if str(mode).lower() == "deep" else "compact"
        if clean_mode == "compact":
            summary_val = [
                f"Modified {file_path} (+{added}/-{removed} lines)",
                f"Updated symbols: {', '.join(symbols[:3]) if symbols else 'Routine updates'}",
            ]
        else:
            summary_val = f"Modified {file_path} (+{added}/-{removed} lines)"

        return {
            "file_path": file_path,
            "summary": summary_val,
            "why_modified": why,
            "changed_symbols": symbols[:8] if symbols else ["Routine code updates"],
            "breaking_changes": ["None detected via heuristic analysis"],
            "risk_level": risk,
            "mode": clean_mode,
            "provider": "heuristic",
            "fallback_reason": reason,
            "cached": False,
        }

    def analyze_file(
        self,
        project_name: str,
        file_path: str,
        diff_text: str,
        burst_id: int | str | None = None,
        session_id: int | str | None = None,
        mode: str = "deep",
        provider: str | None = None,
        force_refresh: bool = False,
        burst_number: int | str | None = None,
        event_id: int | str | None = None,
    ) -> dict[str, Any]:
        """Analyze a single file diff using AI with fallback and caching."""
        diff_hash = self.compute_diff_hash(diff_text)
        b_id = burst_id if burst_id is not None else 0
        sess_id = session_id if session_id is not None else 1

        # Check cache if not forced refresh; if forced refresh, delete existing cached files
        if force_refresh:
            self.cache_mgr.delete_file_analysis(
                burst_id=b_id,
                file_path=file_path,
                session_id=sess_id,
                project_name=project_name,
                burst_number=burst_number,
                event_id=event_id,
            )
        else:
            cached = self.cache_mgr.get_file_analysis(
                burst_id=b_id,
                file_path=file_path,
                diff_hash=diff_hash,
                session_id=sess_id,
                project_name=project_name,
                burst_number=burst_number,
                event_id=event_id,
            )
            if not cached:
                cached = self.cache_mgr.find_file_analysis(
                    burst_id=b_id,
                    file_path=file_path,
                    session_id=sess_id,
                    project_name=project_name,
                    diff_hash=diff_hash,
                    burst_number=burst_number,
                    event_id=event_id,
                )
            if cached:
                # If user runs deep analysis on a file with only compact cache, replace it
                if mode == "deep" and cached.get("mode") == "compact":
                    logger.info("Cached analysis is compact; replacing with deep analysis for %s", file_path)
                    cached = None
                else:
                    cached["cached"] = True
                    return cached

        cfg = self.load_config(self.engine_root)
        pref_provider = (provider or cfg.get("preferred_provider") or cfg.get("default_provider", "gemini")).lower()

        if pref_provider in ("heuristic", "none"):
            fallback = self._fallback_file_analysis(
                file_path=file_path,
                diff_text=diff_text,
                mode=mode,
                reason="heuristic_mode",
            )
            fallback["provider"] = "heuristic"
            fallback["diff_hash"] = diff_hash
            self.cache_mgr.save_file_analysis(
                burst_id=b_id,
                file_path=file_path,
                diff_hash=diff_hash,
                analysis=fallback,
                session_id=sess_id,
                project_name=project_name,
                burst_number=burst_number,
                event_id=event_id,
            )
            return fallback

        chunked_diff = self.chunk_diff(diff_text, max_lines=400, max_chars=30000)
        prompt = build_file_diff_prompt(
            file_path=file_path,
            diff_text=chunked_diff,
            project_name=project_name,
            mode=mode,
        )

        has_gemini = bool(cfg.get("gemini", {}).get("api_key") or os.environ.get("GEMINI_API_KEY"))
        providers_to_try = []
        if pref_provider == "gemini" and has_gemini:
            providers_to_try = ["gemini", "ollama"]
        elif pref_provider == "ollama":
            providers_to_try = ["ollama", "gemini"] if has_gemini else ["ollama"]
        else:
            providers_to_try = ["gemini"] if has_gemini else ["ollama"]

        last_error = ""
        result = None

        for p_name_curr in providers_to_try:
            try:
                if p_name_curr == "gemini":
                    rate_status = self.rate_limiter.get_status()
                    if rate_status["cooling_down"] and rate_status["cooldown_remaining_s"] > 3.0:
                        logger.info("Gemini cooling down (%.1fs); skipping", rate_status["cooldown_remaining_s"])
                        last_error = f"Gemini cooling down ({rate_status['cooldown_remaining_s']}s remaining)"
                        continue
                    logger.info("Analyzing file %s via Gemini API...", file_path)
                    raw_res = self._call_gemini(prompt)
                elif p_name_curr == "ollama":
                    logger.info("Analyzing file %s via local Ollama...", file_path)
                    raw_res = self._call_ollama(prompt)
                else:
                    continue

                if raw_res:
                    summary_raw = raw_res.get("summary")
                    summary_text = summary_raw if isinstance(summary_raw, str) else (" ".join(summary_raw) if isinstance(summary_raw, list) else str(summary_raw or ""))

                    raw_symbols = raw_res.get("changed_symbols") or []
                    if isinstance(raw_symbols, str):
                        raw_symbols = [s.strip() for s in raw_symbols.split(",") if s.strip()]
                    elif not isinstance(raw_symbols, list):
                        raw_symbols = [str(raw_symbols)]

                    raw_breaking = raw_res.get("breaking_changes") or ["None detected"]
                    if isinstance(raw_breaking, str):
                        raw_breaking = [raw_breaking]
                    elif not isinstance(raw_breaking, list):
                        raw_breaking = [str(raw_breaking)]

                    result = {
                        "file_path": file_path,
                        "summary": summary_text or f"Changes in {file_path}",
                        "why_modified": str(raw_res.get("why_modified") or raw_res.get("intent") or "Code changes made during development burst."),
                        "changed_symbols": [str(s) for s in raw_symbols],
                        "breaking_changes": [str(b) for b in raw_breaking],
                        "risk_level": str(raw_res.get("risk_level") or "LOW").upper(),
                        "mode": mode,
                        "provider": raw_res.get("provider", p_name_curr),
                        "cached": False,
                    }
                    break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Provider '%s' failed for file %s: %s", p_name_curr, file_path, exc)

        if not result:
            result = self._fallback_file_analysis(
                file_path=file_path,
                diff_text=diff_text,
                mode=mode,
                reason=f"Providers unreachable ({last_error})",
            )

        result["cached"] = False
        result["diff_hash"] = diff_hash

        self.cache_mgr.save_file_analysis(
            burst_id=b_id,
            file_path=file_path,
            diff_hash=diff_hash,
            analysis=result,
            session_id=sess_id,
            project_name=project_name,
            burst_number=burst_number,
            event_id=event_id,
        )
        return result


