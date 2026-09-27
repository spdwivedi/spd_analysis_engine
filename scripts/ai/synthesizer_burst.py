"""
scripts/ai/synthesizer_burst.py
================================
BurstSynthesizerMixin: analyze_burst and analyze_burst_overview methods
extracted from AISynthesizer to keep individual modules under 500 lines.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

try:
    from .prompt_builder import is_sanitized_patch
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ai.prompt_builder import is_sanitized_patch
    except (ImportError, ModuleNotFoundError):
        from scripts.ai.prompt_builder import is_sanitized_patch

logger = logging.getLogger(__name__)


class BurstSynthesizerMixin:
    """
    Mixin providing burst-level AI analysis methods for AISynthesizer.
    Requires self.engine_root, self.cache_mgr, self.rate_limiter,
    self.compute_diff_hash, self.chunk_diff, self._call_gemini, self._call_ollama,
    self._fallback_analysis, self.load_config from the base class.
    """

    def analyze_burst(
        self,
        *args: Any,
        project_name: str | None = None,
        workspace_path: Path | str | None = None,
        event: dict[str, Any] | None = None,
        patches: list[dict[str, Any]] | None = None,
        previous_summary: str | dict[str, Any] | None = None,
        provider: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Analyze an atomic code burst event with deep architectural context and strict diff sanitization.
        """
        if len(args) >= 1:
            if isinstance(args[0], str):
                project_name = args[0]
                if len(args) >= 2:
                    workspace_path = args[1]
                if len(args) >= 3 and isinstance(args[2], dict):
                    event = args[2]
                if len(args) >= 4:
                    previous_summary = args[3]
            elif isinstance(args[0], dict):
                event = args[0]
                if len(args) >= 2 and isinstance(args[1], list):
                    patches = args[1]
                if len(args) >= 3 and isinstance(args[2], str):
                    provider = args[2]

        if event is None:
            event = {}

        if patches is None:
            if "patches" in event and isinstance(event["patches"], list):
                patches = event["patches"]
            else:
                patches = []

        sanitized_patches = [
            p for p in patches
            if is_sanitized_patch(p.get("file_path", ""))
        ]

        files = [p.get("file_path", "") for p in sanitized_patches if p.get("file_path")]
        first_file = event.get("first_file_touched")
        summary = event.get("summary")

        diff_blocks = []
        for p in sanitized_patches:
            fp = p.get("file_path", "unknown")
            diff = p.get("diff_content") or ""
            diff_blocks.append(f"diff --git a/{fp} b/{fp}\n{diff}")

        full_diff = "\n\n".join(diff_blocks).strip()

        delta_diff = ""
        if project_name and (workspace_path or event.get("workspace_path")):
            w_path = workspace_path or event.get("workspace_path")
            slug = str(project_name).lower().replace(" ", "_").replace("-", "_")
            for tier in ("current", "last_run"):
                cand_shadow = self.engine_root / tier / slug / "shadow_git"
                if cand_shadow.exists():
                    try:
                        from spd_analysis_engine.scripts.git_shadow import ShadowGit
                        sg = ShadowGit(shadow_dir=cand_shadow, target_dir=w_path)
                        delta_diff = sg.get_burst_delta_diff(event_id=event.get("id"), files=files)
                        if delta_diff and delta_diff.strip():
                            break
                    except Exception as s_exc:
                        logger.debug("Could not extract ShadowGit delta diff: %s", s_exc)

        if delta_diff and delta_diff.strip():
            full_diff = delta_diff.strip()

        if not full_diff:
            return self._fallback_analysis(
                files, first_file, summary,
                reason="No non-noise diff content after sanitization",
                project_name=project_name,
                workspace_path=workspace_path,
            )

        sess_id = event.get("session_id") if isinstance(event, dict) else kwargs.get("session_id")
        burst_num = (event.get("burst_num") or event.get("id")) if isinstance(event, dict) else kwargs.get("burst_id")
        p_name = project_name or (event.get("project_name") if isinstance(event, dict) else kwargs.get("project_name"))
        diff_hash = self.compute_diff_hash(full_diff)
        cached = self.get_cached_analysis(
            diff_hash,
            project_name=p_name,
            session_id=sess_id,
            burst_id=burst_num,
        )
        if cached:
            return cached

        tree = self.generate_directory_tree(workspace_path)
        tech_stack = self.detect_tech_stack(workspace_path)

        executed_commands: list[str] = []
        if project_name:
            slug = str(project_name).lower().replace(" ", "_").replace("-", "_")
            db_cand = None
            for tier in ("current", "last_run"):
                cand = self.engine_root / tier / slug / "session.db"
                if cand.exists():
                    db_cand = cand
                    break
            if db_cand:
                try:
                    conn = sqlite3.connect(f"file:{db_cand}?mode=ro", uri=True)
                    cur = conn.cursor()
                    curr_id = event.get("id")
                    session_id = event.get("session_id")
                    if curr_id is not None:
                        if session_id is not None:
                            cur.execute(
                                "SELECT first_file_touched, summary FROM events WHERE event_type = 'EXEC' AND session_id = ? AND id < ? ORDER BY id DESC LIMIT 5",
                                (session_id, curr_id)
                            )
                        else:
                            cur.execute(
                                "SELECT first_file_touched, summary FROM events WHERE event_type = 'EXEC' AND id < ? ORDER BY id DESC LIMIT 5",
                                (curr_id,)
                            )
                        for r in reversed(cur.fetchall()):
                            cmd = r[0] or r[1] or ""
                            if cmd and cmd not in executed_commands:
                                executed_commands.append(cmd)
                    conn.close()
                except Exception as q_exc:
                    logger.debug("Failed querying EXEC commands for burst: %s", q_exc)

        chunked_diff = self.chunk_diff(full_diff)
        prompt = self._build_prompt(
            chunked_diff,
            first_file=first_file,
            summary=summary,
            files=files,
            project_name=project_name,
            workspace_path=workspace_path,
            tech_stack=tech_stack,
            dir_tree=tree,
            previous_summary=previous_summary,
            executed_commands=executed_commands,
        )

        cfg = self.load_config(self.engine_root)
        pref_provider = (provider or cfg.get("preferred_provider") or cfg.get("default_provider", "gemini")).lower()

        if pref_provider in ("heuristic", "none"):
            fallback = self._fallback_analysis(
                files, first_file, summary,
                reason="heuristic_mode",
                project_name=project_name,
                workspace_path=workspace_path,
            )
            fallback["provider"] = "heuristic"
            self.save_cached_analysis(
                diff_hash,
                fallback,
                project_name=p_name,
                session_id=sess_id,
                burst_id=burst_num,
            )
            return fallback

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
                        logger.info("Gemini is cooling down (%.1fs remaining); skipping to next provider.", rate_status["cooldown_remaining_s"])
                        last_error = f"Gemini cooling down ({rate_status['cooldown_remaining_s']}s remaining)"
                        continue
                    logger.info("Attempting deep AI synthesis via Gemini API...")
                    result = self._call_gemini(prompt)
                    break
                elif p_name_curr == "ollama":
                    logger.info("Attempting deep AI synthesis via local Ollama...")
                    result = self._call_ollama(prompt)
                    break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Provider '%s' failed for diff %s: %s", p_name_curr, diff_hash[:8], exc)

        if not result:
            result = self._fallback_analysis(
                files, first_file, summary,
                reason=f"Providers unreachable ({last_error})",
                project_name=project_name,
                workspace_path=workspace_path,
            )

        result["cached"] = False
        result["diff_hash"] = diff_hash

        self.save_cached_analysis(
            diff_hash,
            result,
            project_name=p_name,
            session_id=sess_id,
            burst_id=burst_num,
        )
        return result

    analyze_event = analyze_burst


    def analyze_burst_overview(
        self,
        project_name: str,
        burst_id: int | str,
        patches: list[dict[str, Any]] | None = None,
        session_id: int | str | None = None,
        provider: str | None = None,
        force_refresh: bool = False,
        burst_number: int | str | None = None,
        event_id: int | str | None = None,
    ) -> dict[str, Any]:
        """
        Generate a compact, high-level overview dictionary { file_path: summary }
        for all touched files in a burst without verbose line diffs.
        Strictly isolates diff extraction to burst_id only.
        """
        slug = str(project_name).lower().replace(" ", "_").replace("-", "_")
        b_id = int(burst_id) if str(burst_id).isdigit() else burst_id
        sess_id = int(session_id) if session_id is not None and str(session_id).isdigit() else (session_id or 1)

        # Strictly isolate diff extraction to burst_id only from session.db if patches not supplied
        if patches is None:
            patches = []
            for tier in ("current", "last_run"):
                db_cand = self.engine_root / tier / slug / "session.db"
                if db_cand.exists():
                    try:
                        target_eid = event_id
                        if target_eid is None:
                            try:
                                from spd_analysis_engine.scripts.server.db_queries import resolve_burst_event
                            except (ImportError, ModuleNotFoundError):
                                try:
                                    from scripts.server.db_queries import resolve_burst_event
                                except (ImportError, ModuleNotFoundError):
                                    resolve_burst_event = None
                            if resolve_burst_event:
                                resolved_eid, resolved_bnum, resolved_sid = resolve_burst_event(
                                    db_cand, sess_id, b_id, event_id=event_id, burst_number=burst_number
                                )
                                target_eid = resolved_eid or b_id
                                if resolved_bnum and burst_number is None:
                                    burst_number = resolved_bnum
                                if resolved_sid:
                                    sess_id = resolved_sid
                            else:
                                target_eid = b_id

                        conn = sqlite3.connect(f"file:{db_cand}?mode=ro", uri=True)
                        conn.row_factory = sqlite3.Row
                        cur = conn.cursor()
                        cur.execute("SELECT file_path, diff_content FROM patches WHERE event_id = ?", (target_eid,))
                        patches = [dict(r) for r in cur.fetchall()]
                        if not patches:
                            b_idx = None
                            try:
                                b_idx = int(burst_number if burst_number is not None else b_id)
                            except (ValueError, TypeError):
                                pass
                            if b_idx and b_idx > 0:
                                cur.execute(
                                    "SELECT id FROM events WHERE session_id = ? AND (event_type = 'EDIT' OR event_type IS NULL) ORDER BY id ASC",
                                    (sess_id,),
                                )
                                edit_eids = [int(r["id"]) for r in cur.fetchall()]
                                if 1 <= b_idx <= len(edit_eids):
                                    mapped_eid = edit_eids[b_idx - 1]
                                    cur.execute("SELECT file_path, diff_content FROM patches WHERE event_id = ?", (mapped_eid,))
                                    patches = [dict(r) for r in cur.fetchall()]
                                    if patches:
                                        target_eid = mapped_eid
                                        if burst_number is None:
                                            burst_number = b_idx
                        conn.close()
                        break
                    except Exception as e:
                        logger.debug("Failed querying patches for burst %s: %s", burst_id, e)

        if not patches:
            return {
                "burst_id": b_id,
                "session_id": sess_id,
                "file_count": 0,
                "overview": {},
                "burst_summary": "No file changes in this burst.",
                "cached": False,
                "provider": "heuristic",
            }

        all_diffs = "".join((p.get("diff_content") or "") for p in patches)
        burst_hash = self.compute_diff_hash(all_diffs or str(b_id))

        if force_refresh:
            self.cache_mgr.delete_burst_overview(
                burst_id=b_id,
                session_id=sess_id,
                project_name=slug,
                burst_number=burst_number,
                event_id=event_id,
            )
        else:
            cached = self.cache_mgr.get_burst_overview(
                burst_id=b_id,
                patch_hash=burst_hash,
                session_id=sess_id,
                project_name=slug,
                burst_number=burst_number,
                event_id=event_id,
            )
            if not cached:
                cached = self.cache_mgr.find_burst_overview(
                    burst_id=b_id,
                    session_id=sess_id,
                    project_name=slug,
                    patch_hash=burst_hash,
                    burst_number=burst_number,
                    event_id=event_id,
                )
            if cached:
                cached["cached"] = True
                if "file_count" not in cached:
                    cached["file_count"] = len(cached.get("overview", {}))
                return cached

        cfg = self.load_config(self.engine_root)
        pref_provider = (provider or cfg.get("preferred_provider") or cfg.get("default_provider", "gemini")).lower()

        def _heuristic_overview():
            overview_map = {}
            for p in patches:
                fp = p.get("file_path", "unknown")
                diff = p.get("diff_content") or ""
                added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
                removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
                overview_map[fp] = f"Modified component with +{added}/-{removed} line changes."
            return {
                "burst_id": b_id,
                "session_id": sess_id,
                "file_count": len(patches),
                "overview": overview_map,
                "burst_summary": f"Burst modification affecting {len(patches)} file(s).",
                "provider": "heuristic",
                "cached": False,
            }

        if pref_provider in ("heuristic", "none"):
            res = _heuristic_overview()
            self.cache_mgr.save_burst_overview(
                burst_id=b_id,
                patch_hash=burst_hash,
                overview=res,
                session_id=sess_id,
                project_name=slug,
                burst_number=burst_number,
                event_id=event_id,
            )
            return res

        file_entries = []
        for p in patches:
            fp = p.get("file_path", "unknown")
            diff = self.chunk_diff(p.get("diff_content") or "", max_lines=60, max_chars=3000)
            file_entries.append(f"--- File: {fp} ---\n{diff}")
        files_text = "\n\n".join(file_entries)

        prompt = f"""You are a principal software architect.
Provide a clear, technical, yet accessible high-level architectural overview of this code modification burst.
Explain what changed across components in this burst, why these changes were introduced, and how the modified components interact with each other.

Project: {slug}
Session: {sess_id}
Burst ID: {b_id}

Modified Files and Diffs in this burst:
{files_text}

Instructions:
Respond with ONLY a valid, parseable JSON object matching this schema:
{{
  "burst_summary": "Clear, technical, yet accessible high-level explanation of what changed in this burst, why, and how components interact",
  "overview": {{
    "path/to/file": "Clear 1-2 sentence technical summary of what this component change does and its role in the burst"
  }}
}}"""

        has_gemini = bool(cfg.get("gemini", {}).get("api_key") or os.environ.get("GEMINI_API_KEY"))
        providers_to_try = []
        if pref_provider == "gemini" and has_gemini:
            providers_to_try = ["gemini", "ollama"]
        elif pref_provider == "ollama":
            providers_to_try = ["ollama", "gemini"] if has_gemini else ["ollama"]
        else:
            providers_to_try = ["gemini"] if has_gemini else ["ollama"]

        result = None
        for p_name in providers_to_try:
            try:
                if p_name == "gemini":
                    rate_status = self.rate_limiter.get_status()
                    if rate_status["cooling_down"] and rate_status["cooldown_remaining_s"] > 3.0:
                        continue
                    raw_res = self._call_gemini(prompt)
                elif p_name == "ollama":
                    raw_res = self._call_ollama(prompt)
                else:
                    continue

                if raw_res and "overview" in raw_res:
                    overview_dict = raw_res.get("overview", {})
                    for p in patches:
                        fp = p.get("file_path", "")
                        if fp and fp not in overview_dict:
                            overview_dict[fp] = "Modified file in burst."
                    result = {
                        "burst_id": b_id,
                        "session_id": sess_id,
                        "file_count": len(patches),
                        "overview": overview_dict,
                        "burst_summary": str(raw_res.get("burst_summary") or f"Burst modification affecting {len(patches)} file(s)."),
                        "provider": raw_res.get("provider", p_name),
                        "cached": False,
                    }
                    break
            except Exception as exc:
                logger.warning("Provider %s failed for burst overview: %s", p_name, exc)

        if not result:
            result = _heuristic_overview()

        self.cache_mgr.save_burst_overview(
            burst_id=b_id,
            patch_hash=burst_hash,
            overview=result,
            session_id=sess_id,
            project_name=slug,
            burst_number=burst_number,
            event_id=event_id,
        )
        return result


