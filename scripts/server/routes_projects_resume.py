"""
scripts/server/routes_projects_resume.py
=========================================
ProjectResumeRoutesMixin: project resumption handler extracted from ProjectRoutesMixin.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    from spd_analysis_engine.scripts.storage_rotator import _slug, read_project_meta
except (ImportError, ModuleNotFoundError):
    from scripts.storage_rotator import _slug, read_project_meta

logger = logging.getLogger(__name__)


class ProjectResumeRoutesMixin:
    """
    Mixin providing project resume endpoint handler.
    Requires self.server_instance, self._resolve_target_path, self._send_json, self._send_error.
    """

    def _handle_api_resume_project(self, project_name: str, body: dict[str, Any] | None = None) -> None:
        """Resume monitoring an inactive project using its saved project.meta & status options."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        # Check if already active
        live = engine.supervisor.get_status()
        for w in live:
            if w.get("project") == slug and w.get("alive"):
                self._send_json({"success": True, "message": "Project is already active.", "already_running": True, "project": slug})  # type: ignore[attr-defined]
                return

        # Locate project.meta across tiers
        target_path = ""
        meta_candidates = [
            engine.engine_root / "current" / slug / "project.meta",
            engine.engine_root / "last_run" / slug / "project.meta",
        ]
        hist_dir = engine.engine_root / "history" / slug
        if hist_dir.exists():
            runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
            if runs:
                meta_candidates.append(runs[-1] / "project.meta")

        for mf in meta_candidates:
            if mf.exists():
                try:
                    for line in mf.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.startswith("target_path="):
                            target_path = line.split("=", 1)[1].strip()
                            break
                    if target_path:
                        break
                except Exception:
                    pass

        # Also fallback to resolving target path via existing resolver if needed
        if not target_path:
            resolved_p = self._resolve_target_path(slug)  # type: ignore[attr-defined]
            if resolved_p:
                target_path = str(resolved_p)

        if not target_path:
            self._send_error(f"Cannot resume '{slug}': project.meta not found.", status=404)  # type: ignore[attr-defined]
            return

        target_p = Path(target_path)
        if not target_p.exists():
            self._send_error(f"Target workspace '{target_path}' does not exist on disk.", status=400)  # type: ignore[attr-defined]
            return

        # Attempt to load saved options from body, project.meta, or worker.status.json
        debounce = 3.5
        track_reads = True
        track_exec = True
        shadow_git = True
        ide_profile = "antigravity"
        ide_custom_marker = None

        meta = read_project_meta(engine.engine_root, slug)
        if meta.get("ide_profile"):
            ide_profile = meta["ide_profile"]
        if meta.get("ide_custom_marker"):
            ide_custom_marker = meta["ide_custom_marker"]

        body_dict = body if isinstance(body, dict) else {}
        db_candidate = body_dict.get("debounce_window") or body_dict.get("debounce")
        if db_candidate is not None:
            try:
                debounce = float(db_candidate)
            except (ValueError, TypeError):
                pass
        elif meta.get("debounce_window") or meta.get("debounce"):
            try:
                debounce = float(meta.get("debounce_window") or meta.get("debounce"))
            except (ValueError, TypeError):
                pass

        if meta.get("powers"):
            try:
                p_meta = json.loads(meta["powers"]) if isinstance(meta["powers"], str) else meta["powers"]
                if isinstance(p_meta, dict):
                    track_reads = p_meta.get("track_reads", track_reads)
                    track_exec = p_meta.get("track_exec", track_exec)
                    shadow_git = p_meta.get("shadow_git", shadow_git)
            except Exception:
                pass

        status_candidates = [
            engine.engine_root / "last_run" / slug / "worker.status.json",
            engine.engine_root / "current" / slug / "worker.status.json",
        ]
        for sf in status_candidates:
            if sf.exists():
                try:
                    s_data = json.loads(sf.read_text(encoding="utf-8"))
                    powers = s_data.get("powers", {})
                    track_reads = powers.get("reads", powers.get("track_reads", track_reads))
                    track_exec = powers.get("exec", powers.get("track_exec", track_exec))
                    shadow_git = powers.get("shadow_git", shadow_git)
                    if "ide_profile" in powers:
                        ide_profile = powers["ide_profile"]
                    if "ide_custom_marker" in powers:
                        ide_custom_marker = powers["ide_custom_marker"]
                    if db_candidate is None and not (meta.get("debounce_window") or meta.get("debounce")):
                        db_info = s_data.get("debounce", {})
                        if isinstance(db_info, dict) and "quiet_period" in db_info:
                            debounce = float(db_info["quiet_period"])
                    break
                except Exception:
                    pass

        try:
            res = engine.supervisor.start_project(
                name=slug,
                path=str(target_p),
                scaffold=False,
                debounce=debounce,
                track_reads=track_reads,
                track_exec=track_exec,
                shadow_git=shadow_git,
                git_init_primary=False,
                ide_profile=ide_profile,
                ide_custom_marker=ide_custom_marker,
            )
            self._send_json({"success": True, "status": "started", "project": slug, "result": res})  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Failed to resume project %s", slug)
            self._send_error(f"Failed to resume project: {exc}", status=500)  # type: ignore[attr-defined]


__all__ = ["ProjectResumeRoutesMixin"]
