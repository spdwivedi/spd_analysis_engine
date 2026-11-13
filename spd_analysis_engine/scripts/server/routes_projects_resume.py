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

from spd_analysis_engine.scripts.storage_rotator import (
    _slug,
    read_project_meta,
    update_project_meta,
    synthesize_project_meta,
)
from spd_analysis_engine.scripts.storage.project_storage import (
    read_project_config,
    write_project_config,
)
from spd_analysis_engine.scripts.core.project_config import ProjectConfig, DEFAULT_DEBOUNCE_S

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
            w_slug = w.get("slug") or w.get("project_slug") or _slug(w.get("project", ""))
            if w_slug == slug and w.get("alive"):
                self._send_json({"success": True, "message": "Project is already active.", "already_running": True, "project": slug})  # type: ignore[attr-defined]
                return

        # Locate project.meta or project.config.json across tiers
        saved_cfg = read_project_config(engine.engine_root, slug)
        target_path = str(saved_cfg.get("target_path", "")).strip()
        if not target_path:
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

        # Self-healing fallback: reconstruct project.meta from session.db, history, or status
        if not target_path:
            synth = synthesize_project_meta(engine.engine_root, slug)
            if synth and synth.get("target_path"):
                target_path = str(synth["target_path"]).strip()

        if not target_path:
            self._send_error(f"Cannot resume '{slug}': project.meta not found.", status=404)  # type: ignore[attr-defined]
            return

        target_p = Path(target_path)
        if not target_p.exists():
            self._send_error(f"Target workspace '{target_path}' does not exist on disk.", status=400)  # type: ignore[attr-defined]
            return

        meta = read_project_meta(engine.engine_root, slug)
        persisted = dict(saved_cfg)
        persisted.update(meta)
        if "debounce" in saved_cfg and "debounce" not in meta:
            persisted["debounce"] = saved_cfg["debounce"]
        if "debounce_s" in saved_cfg and "debounce_s" not in meta:
            persisted["debounce_s"] = saved_cfg["debounce_s"]

        body_dict = body if isinstance(body, dict) else {}
        cfg = ProjectConfig.resolve(cli_overrides=body_dict, persisted_config=persisted)

        # If debounce was not in body, meta or saved_cfg, check worker.status.json
        if "debounce_window" not in body_dict and "debounce" not in body_dict and "debounce_window" not in persisted and "debounce" not in persisted:
            status_candidates = [
                engine.engine_root / "last_run" / slug / "worker.status.json",
                engine.engine_root / "current" / slug / "worker.status.json",
            ]
            for sf in status_candidates:
                if sf.exists():
                    try:
                        with open(sf, "r", encoding="utf-8") as f:
                            s_data = json.load(f)
                        db_info = s_data.get("debounce", {})
                        if isinstance(db_info, dict) and "quiet_period" in db_info:
                            cfg.update_from_dict({"debounce_s": db_info["quiet_period"]})
                        break
                    except Exception:
                        pass

        debounce = cfg.debounce_s
        track_reads = cfg.watch_policy.get("track_reads", True)
        track_exec = cfg.watch_policy.get("track_exec", True)
        shadow_git = cfg.git_policy.get("shadow_git", True)
        ide_profile = cfg.ai_profile
        ide_custom_marker = body_dict.get("ide_custom_marker") or meta.get("ide_custom_marker") or saved_cfg.get("ide_custom_marker") or None

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
            update_project_meta(
                engine.engine_root,
                slug,
                target_path=str(target_p),
                debounce=str(debounce),
                debounce_window=str(debounce),
                ide_profile=ide_profile,
            )
            cfg_data = dict(saved_cfg)
            cfg_data.update({
                "slug": slug,
                "display_name": saved_cfg.get("display_name") or meta.get("project_name") or slug,
                "target_path": str(target_p),
                "debounce": debounce,
                "debounce_s": debounce,
                "debounce_window": debounce,
                "ide_profile": ide_profile,
                "powers": cfg.powers,
                "git_policy": cfg.git_policy,
                "watch_policy": cfg.watch_policy,
            })
            if ide_custom_marker:
                cfg_data["ide_custom_marker"] = ide_custom_marker
            write_project_config(engine.engine_root, slug, cfg_data)

            self._send_json({"success": True, "status": "started", "project": slug, "result": res})  # type: ignore[attr-defined]
        except ValueError as exc:
            if "already running" in str(exc).lower():
                self._send_json({"success": True, "message": "Project is already running.", "project": slug})  # type: ignore[attr-defined]
                return
            self._send_json({"success": False, "error": str(exc)}, status=400)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Failed to resume project %s", slug)
            self._send_error(f"Failed to resume project: {exc}", status=500)  # type: ignore[attr-defined]


__all__ = ["ProjectResumeRoutesMixin"]
