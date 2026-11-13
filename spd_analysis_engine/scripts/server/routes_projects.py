"""
scripts/server/routes_projects.py
=================================
Project lifecycle routes mixin for the SPD Analysis Engine.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from spd_analysis_engine.scripts.storage_rotator import (
    StorageRotator,
    SessionDB,
    _slug,
    read_project_meta,
    update_project_meta,
    consolidate_project_history,
    resolve_project_context,
)
from spd_analysis_engine.scripts.git_shadow import ShadowGit, GitCredentialManager
from spd_analysis_engine.scripts.shell_interceptor import ExecutionTracker
from spd_analysis_engine.scripts.ai_analyzer import AISynthesizer
from spd_analysis_engine.scripts.storage.project_storage import (
    read_project_config,
    write_project_config,
)
from spd_analysis_engine.scripts.core.project_config import ProjectConfig, DEFAULT_DEBOUNCE_S

from .routes_projects_resume import ProjectResumeRoutesMixin

try:
    from spd_analysis_engine.scripts.git.milestone_tracker import get_project_milestone_history
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.git.milestone_tracker import get_project_milestone_history
    except (ImportError, ModuleNotFoundError):
        get_project_milestone_history = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

try:
    from spd_analysis_engine.scripts.core.time_utils import utc_now_iso as _utcnow_iso
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.core.time_utils import utc_now_iso as _utcnow_iso
    except (ImportError, ModuleNotFoundError):
        from datetime import datetime, timezone as _tz
        def _utcnow_iso() -> str:  # type: ignore[misc]
            return datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

_now_ist_iso = _utcnow_iso  # legacy alias kept for any remaining call-sites



class ProjectRoutesMixin(ProjectResumeRoutesMixin):
    """
    Mixin containing project lifecycle, session retrieval, git sync,
    burst rollback/sealing, and project specs handlers.
    Expected to be mixed into _EngineRequestHandler.
    """

    def _resolve_target_path(self, slug: str) -> Path | None:
        """Resolve workspace directory path for a project slug across active/persisted state."""
        clean_slug = _slug(slug)
        engine = self.server_instance  # type: ignore[attr-defined]
        # 1. Check active worker status in supervisor
        if hasattr(engine, "supervisor") and engine.supervisor:
            try:
                statuses = engine.supervisor.get_status()
                for s in statuses:
                    s_slug = s.get("slug") or s.get("project_slug") or _slug(s.get("project", ""))
                    if s_slug == clean_slug and (s.get("root_path") or s.get("target_path")):
                        p = Path(s.get("root_path") or s["target_path"])
                        if p.exists():
                            return p.resolve()
            except Exception:
                pass

        # 2. Resolve via resolve_project_context across current, last_run, history
        ctx = resolve_project_context(clean_slug, engine.engine_root)
        if ctx.get("target_path") and ctx["target_path"].exists():
            return ctx["target_path"].resolve()

        # 3. Check project.meta
        meta = read_project_meta(engine.engine_root, clean_slug)
        if meta.get("target_path"):
            p = Path(meta["target_path"])
            if p.exists():
                return p.resolve()

        return None

    def _resolve_db_path(self, slug: str) -> Path | None:
        """Resolve session.db path across current, last_run, and history tiers."""
        engine = self.server_instance  # type: ignore[attr-defined]
        ctx = resolve_project_context(_slug(slug), engine.engine_root)
        return ctx.get("db_path")

    def _resolve_shadow_git_path(self, slug: str) -> Path | None:
        """Resolve shadow_git repository path across tiers."""
        engine = self.server_instance  # type: ignore[attr-defined]
        ctx = resolve_project_context(_slug(slug), engine.engine_root)
        return ctx.get("shadow_git_path")

    def _handle_api_status(self) -> None:
        """Return supervisor status, workers, and system metrics."""
        engine = self.server_instance  # type: ignore[attr-defined]
        sup = engine.supervisor
        statuses = sup.get_status()

        active_workers = [s for s in statuses if s.get("alive")]
        total_mem = sum(s.get("memory_mb") or 0.0 for s in active_workers)

        data = {
            "status": "ok",
            "server_time": _utcnow_iso(),
            "active_count": len(active_workers),
            "total_workers": len(statuses),
            "workers": statuses,
            "system": {
                "platform": sys.platform,
                "engine_root": str(engine.engine_root),
                "total_worker_memory_mb": round(total_mem, 1),
            },
            "ai_rate_limit": AISynthesizer.rate_limiter.get_status(),
        }
        self._send_json(data)  # type: ignore[attr-defined]

    def _handle_api_projects(self) -> None:
        """Return all known projects grouped by tier with metadata."""
        engine = self.server_instance  # type: ignore[attr-defined]
        rotator = engine.rotator
        sup = engine.supervisor

        # 1. Fetch live worker statuses from supervisor
        live_workers = sup.get_status()
        live_statuses = {
            (s.get("slug") or s.get("project_slug") or _slug(s.get("project", ""))): s
            for s in live_workers
        }

        all_projects = rotator.list_all()

        grouped: dict[str, list[dict[str, Any]]] = {
            "current": [],
            "last_run": [],
            "history": [],
        }

        for slug, tiers in all_projects.items():
            status_entry = live_statuses.get(slug, {})
            is_active = bool(slug in live_statuses and status_entry.get("alive") and status_entry.get("pid"))
            if not is_active and status_entry.get("pid"):
                try:
                    if sup._verify_worker_pid(status_entry["pid"], slug) is not False:
                        is_active = True
                except Exception:
                    pass

            # Count prompt burst events (EDIT events) in DB
            db_path = None
            if tiers.get("current"):
                db_path = engine.engine_root / "current" / slug / "session.db"
            elif tiers.get("last_run"):
                db_path = engine.engine_root / "last_run" / slug / "session.db"

            events_count = engine._count_events_in_db(db_path) if db_path else 0

            # Target path & metadata lookup
            meta = read_project_meta(engine.engine_root, slug)
            target_path = status_entry.get("target_path", "") or meta.get("target_path", "")

            # Check for crash / ungraceful termination sentinel
            current_lock = engine.engine_root / "current" / slug / "session.lock"
            last_run_lock = engine.engine_root / "last_run" / slug / "session.lock"
            clean_exit = meta.get("clean_exit", "").lower()

            is_interrupted = False
            if not is_active and (tiers.get("current") or tiers.get("last_run")):
                if current_lock.exists() or last_run_lock.exists() or clean_exit == "false":
                    is_interrupted = True
                    logger.warning("Project '%s': Session was terminated unexpectedly (e.g. system reboot, task kill, power outage).", slug)

            state = "INTERRUPTED" if is_interrupted else ("ACTIVE" if is_active else "STOPPED")
            display_name = meta.get("project_name") or status_entry.get("display_name") or slug
            project_id = meta.get("project_id") or status_entry.get("project_id") or f"proj_{slug}"
            root_path = str(Path(target_path).resolve()) if target_path else ""

            item = {
                "project_id": project_id,
                "slug": slug,
                "project_slug": slug,
                "display_name": display_name,
                "root_path": root_path,
                "state": state,
                "name": slug,
                "project": display_name,
                "is_active": is_active,
                "status": state,
                "is_interrupted": is_interrupted,
                "interrupted_reason": "Session was terminated unexpectedly (e.g. system reboot, task kill, power outage)." if is_interrupted else "",
                "pid": status_entry.get("pid"),
                "uptime_s": status_entry.get("uptime_s"),
                "memory_mb": status_entry.get("memory_mb"),
                "session_id": status_entry.get("session_id"),
                "events_count": events_count,
                "target_path": target_path,
                "history_runs": tiers.get("history_runs", []),
                "powers": status_entry.get("powers", {}),
                "ide_profile": meta.get("ide_profile", status_entry.get("powers", {}).get("ide_profile", "antigravity")),
                "ide_custom_marker": meta.get("ide_custom_marker", status_entry.get("powers", {}).get("ide_custom_marker", "")),
                "remote_url": meta.get("remote_url", ""),
                "remote_branch": meta.get("remote_branch", "main"),
                "last_push_status": meta.get("last_push_status", "Never pushed"),
                "last_push_time": meta.get("last_push_time", ""),
            }

            # Strictly enforce: ONLY running workers with active PIDs can be in "current"
            if is_active:
                grouped["current"].append(item)
            elif tiers.get("last_run") or tiers.get("current"):
                grouped["last_run"].append(item)
            elif tiers.get("history_runs"):
                grouped["history"].append(item)

        self._send_json(grouped)  # type: ignore[attr-defined]

    def _handle_api_start_project(self, body: dict[str, Any]) -> None:
        name = str(body.get("project", "")).strip()
        path = str(body.get("path", "")).strip()
        scaffold = bool(body.get("scaffold", True))

        if not name:
            self._send_error("Parameter 'project' is required.")  # type: ignore[attr-defined]
            return
        if not path:
            self._send_error("Parameter 'path' is required.")  # type: ignore[attr-defined]
            return

        slug = _slug(name)
        saved_cfg = read_project_config(self.server_instance.engine_root, slug)
        meta = read_project_meta(self.server_instance.engine_root, slug)
        persisted = dict(saved_cfg)
        persisted.update(meta)
        if "debounce" in saved_cfg and "debounce" not in meta:
            persisted["debounce"] = saved_cfg["debounce"]
        if "debounce_s" in saved_cfg and "debounce_s" not in meta:
            persisted["debounce_s"] = saved_cfg["debounce_s"]

        cfg = ProjectConfig.resolve(cli_overrides=body, persisted_config=persisted)
        debounce = cfg.debounce_s
        track_reads = cfg.watch_policy.get("track_reads", True)
        track_exec = cfg.watch_policy.get("track_exec", True)
        shadow_git = cfg.git_policy.get("shadow_git", True)
        git_init_primary = bool(body.get("git_init_primary", False))
        ide_profile = cfg.ai_profile
        ide_custom_marker = str(body.get("ide_custom_marker", "")).strip() or None
        remote_url = str(body.get("remote_url", "")).strip() or str(meta.get("remote_url", "")).strip() or str(saved_cfg.get("remote_url", "")).strip()

        try:
            res = self.server_instance.supervisor.start_project(  # type: ignore[attr-defined]
                name=name,
                path=path,
                scaffold=scaffold,
                debounce=debounce,
                track_reads=track_reads,
                track_exec=track_exec,
                shadow_git=shadow_git,
                git_init_primary=git_init_primary,
                ide_profile=ide_profile,
                ide_custom_marker=ide_custom_marker,
            )
            # Persist project settings to project.config.json and project.meta
            engine_root = self.server_instance.engine_root
            meta_updates: dict[str, Any] = {
                "target_path": path,
                "debounce": str(debounce),
                "debounce_window": str(debounce),
                "ide_profile": ide_profile,
            }
            if remote_url:
                meta_updates["remote_url"] = remote_url
            if ide_custom_marker:
                meta_updates["ide_custom_marker"] = ide_custom_marker
            update_project_meta(engine_root, slug, **meta_updates)

            cfg_data = dict(saved_cfg)
            cfg_data.update({
                "slug": slug,
                "display_name": name,
                "target_path": path,
                "debounce": debounce,
                "debounce_s": debounce,
                "debounce_window": debounce,
                "ide_profile": ide_profile,
                "powers": cfg.powers,
                "git_policy": cfg.git_policy,
                "watch_policy": cfg.watch_policy,
            })
            if remote_url:
                cfg_data["remote_url"] = remote_url
            if ide_custom_marker:
                cfg_data["ide_custom_marker"] = ide_custom_marker
            write_project_config(engine_root, slug, cfg_data)

            self._send_json({"status": "started", "result": res})  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Failed to start project via API")
            self._send_error(str(exc), status=400)  # type: ignore[attr-defined]

    def _handle_api_stop_project(self, body: dict[str, Any]) -> None:
        name = str(body.get("project", "")).strip()
        if not name:
            self._send_error("Parameter 'project' is required.")  # type: ignore[attr-defined]
            return

        try:
            try:
                res = self.server_instance.supervisor.stop_project(name)  # type: ignore[attr-defined]
            except KeyError:
                res = {"status": "not_found", "note": "Worker process not found or already stopped."}
            # Forward the actual status reported by supervisor (may be "stopped" or "interrupted")
            reported_status = res.get("status", "stopped") if isinstance(res, dict) else "stopped"
            self._send_json({"status": reported_status, "result": res})  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Failed to stop project via API")
            self._send_error(str(exc), status=400)  # type: ignore[attr-defined]

    def _handle_api_project_specs(self, project_name: str) -> None:
        """Return project specs, powers, debounce window, and GitHub remote info."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        saved_cfg = read_project_config(engine.engine_root, slug)
        meta = read_project_meta(engine.engine_root, slug)
        persisted = dict(saved_cfg)
        persisted.update(meta)
        if "debounce" in saved_cfg and "debounce" not in meta:
            persisted["debounce"] = saved_cfg["debounce"]
        if "debounce_s" in saved_cfg and "debounce_s" not in meta:
            persisted["debounce_s"] = saved_cfg["debounce_s"]

        target_path = self._resolve_target_path(slug)
        target_path_str = str(target_path) if target_path else (meta.get("target_path") or saved_cfg.get("target_path", ""))

        cfg = ProjectConfig.resolve(persisted_config=persisted)
        debounce = cfg.debounce_s
        powers = dict(cfg.powers)
        ide_profile = cfg.ai_profile
        ide_custom_marker = meta.get("ide_custom_marker") or saved_cfg.get("ide_custom_marker", "")

        # Check live supervisor status
        live = engine.supervisor.get_status()
        for w in live:
            w_slug = w.get("slug") or w.get("project_slug") or _slug(w.get("project", ""))
            if w_slug == slug and w.get("alive"):
                if w.get("powers"):
                    powers = w["powers"]
                    if "ide_profile" in powers:
                        ide_profile = powers["ide_profile"]
                    if "ide_custom_marker" in powers:
                        ide_custom_marker = powers["ide_custom_marker"]
                db_info = w.get("debounce", {})
                if isinstance(db_info, dict) and "quiet_period" in db_info:
                    debounce = float(db_info["quiet_period"])
                break
        else:
            for tier in ("current", "last_run"):
                sf = engine.engine_root / tier / slug / "worker.status.json"
                if sf.exists():
                    try:
                        with open(sf, "r", encoding="utf-8") as f:
                            s_data = json.load(f)
                        if s_data.get("powers"):
                            powers = s_data["powers"]
                            if "ide_profile" in powers:
                                ide_profile = powers["ide_profile"]
                            if "ide_custom_marker" in powers:
                                ide_custom_marker = powers["ide_custom_marker"]
                        db_info = s_data.get("debounce", {})
                        if isinstance(db_info, dict) and "quiet_period" in db_info:
                            debounce = float(db_info["quiet_period"])
                        break
                    except (PermissionError, OSError, json.JSONDecodeError):
                        pass
                    except Exception:
                        pass

        self._send_json({  # type: ignore[attr-defined]
            "project_id": meta.get("project_id") or f"proj_{slug}",
            "slug": slug,
            "project_slug": slug,
            "display_name": meta.get("project_name") or slug,
            "root_path": str(Path(target_path_str).resolve()) if target_path_str else "",
            "project": slug,
            "target_path": target_path_str,
            "debounce": debounce,
            "debounce_window": debounce,
            "monitored_ai_env": meta.get("monitored_ai_env", "Google Antigravity"),
            "powers": powers,
            "ide_profile": ide_profile,
            "ide_custom_marker": ide_custom_marker,
            "remote_url": meta.get("remote_url") or saved_cfg.get("remote_url", ""),
            "remote_branch": meta.get("remote_branch") or saved_cfg.get("remote_branch", "main"),
            "last_push_status": meta.get("last_push_status", "Never pushed"),
            "last_push_time": meta.get("last_push_time", ""),
            "last_push_commit": meta.get("last_push_commit", ""),
            "auto_push": meta.get("auto_push", "false").lower() in ("true", "1", "yes", "on"),
        })

    def _handle_api_project_milestones(self, project_name: str) -> None:
        """Return the most recent git milestones for the specified project."""
        slug = _slug(project_name)
        db_path = self._resolve_db_path(slug)  # type: ignore[attr-defined]
        milestones: list[dict[str, Any]] = []
        if get_project_milestone_history is not None and db_path and db_path.exists():
            try:
                milestones = get_project_milestone_history(db_path, limit=20)
            except Exception as exc:
                logger.warning("Could not query milestone history for %s: %s", slug, exc)

        self._send_json({  # type: ignore[attr-defined]
            "success": True,
            "project": slug,
            "milestones": milestones,
        })

    def _handle_api_unpushed_milestone(self, project_name: str) -> None:
        """Proxy to synthesize_milestone for GET /api/project/<slug>/unpushed-milestone."""
        if hasattr(self, "_handle_api_synthesize_milestone"):
            self._handle_api_synthesize_milestone(project_name, {})  # type: ignore[attr-defined]
        else:
            self._send_error("Milestone synthesis not supported", status=501)  # type: ignore[attr-defined]

    def _handle_api_system_reconcile(self) -> None:
        """Explicit POST endpoint to reconcile stale sessions and consolidate project history."""
        engine = self.server_instance  # type: ignore[attr-defined]
        rotator = engine.rotator
        sup = engine.supervisor

        reconciled_archives: list[str] = []
        consolidated: list[str] = []

        live_workers = sup.get_status()
        live_statuses = {
            (s.get("slug") or s.get("project_slug") or _slug(s.get("project", ""))): s
            for s in live_workers
        }
        current_dir = engine.engine_root / "current"
        if current_dir.exists():
            for proj_dir in list(current_dir.iterdir()):
                if proj_dir.is_dir():
                    slug = proj_dir.name
                    status = live_statuses.get(slug, {})
                    is_running = bool(status.get("alive") and status.get("pid"))
                    if not is_running and status.get("pid"):
                        try:
                            if sup._verify_worker_pid(status["pid"], slug) is not False:
                                is_running = True
                        except Exception:
                            pass
                    if not is_running:
                        try:
                            rotator.archive_project(slug)
                            reconciled_archives.append(slug)
                        except Exception as exc:
                            logger.warning("Could not reconcile %s: %s", slug, exc)

        all_projects = rotator.list_all()
        for slug in all_projects.keys():
            try:
                res = consolidate_project_history(engine.engine_root, slug)
                if res:
                    consolidated.append(slug)
            except Exception as exc:
                logger.warning("Could not consolidate %s: %s", slug, exc)

        self._send_json({  # type: ignore[attr-defined]
            "success": True,
            "reconciled_archives": reconciled_archives,
            "consolidated": consolidated,
        })
