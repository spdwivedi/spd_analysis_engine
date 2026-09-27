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

try:
    from spd_analysis_engine.scripts.storage_rotator import (
        StorageRotator, SessionDB, _slug,
        read_project_meta, update_project_meta, consolidate_project_history,
    )
    from spd_analysis_engine.scripts.git_shadow import ShadowGit, GitCredentialManager
    from spd_analysis_engine.scripts.shell_interceptor import ExecutionTracker
    from spd_analysis_engine.scripts.ai_analyzer import AISynthesizer
except (ImportError, ModuleNotFoundError):
    from scripts.storage_rotator import (
        StorageRotator, SessionDB, _slug,
        read_project_meta, update_project_meta, consolidate_project_history,
    )
    from scripts.git_shadow import ShadowGit, GitCredentialManager
    from scripts.shell_interceptor import ExecutionTracker
    from scripts.ai_analyzer import AISynthesizer

try:
    from .routes_projects_resume import ProjectResumeRoutesMixin
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.server.routes_projects_resume import ProjectResumeRoutesMixin
    except (ImportError, ModuleNotFoundError):
        from scripts.server.routes_projects_resume import ProjectResumeRoutesMixin

logger = logging.getLogger(__name__)

_NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


_utcnow_iso = _now_ist_iso


class ProjectRoutesMixin(ProjectResumeRoutesMixin):
    """
    Mixin containing project lifecycle, session retrieval, git sync,
    burst rollback/sealing, and project specs handlers.
    Expected to be mixed into _EngineRequestHandler.
    """

    def _resolve_target_path(self, slug: str) -> Path | None:
        """Resolve workspace directory path for a project slug across active/persisted state."""
        engine = self.server_instance  # type: ignore[attr-defined]
        # 1. Check active worker status in supervisor
        statuses = engine.supervisor.get_status()
        for s in statuses:
            if s.get("project_slug") == slug and s.get("target_path"):
                p = Path(s["target_path"])
                if p.exists():
                    return p

        # 2. Check metadata across tiers
        for tier in ("current", "last_run"):
            meta = read_project_meta(engine.engine_root, slug)
            if meta.get("target_path"):
                p = Path(meta["target_path"])
                if p.exists():
                    return p

        # 3. Check history
        hist_dir = engine.engine_root / "history" / slug
        if hist_dir.exists():
            runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
            if runs:
                meta = read_project_meta(engine.engine_root, slug)
                if meta.get("target_path"):
                    p = Path(meta["target_path"])
                    if p.exists():
                        return p

        # 4. Fallback: check worker.status.json
        for tier in ("current", "last_run"):
            cand = engine.engine_root / tier / slug / "worker.status.json"
            if cand.exists():
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    if data.get("target_path"):
                        p = Path(data["target_path"])
                        if p.exists():
                            return p
                except Exception:
                    pass

        return None

    def _resolve_db_path(self, slug: str) -> Path | None:
        """Resolve session.db path across current, last_run, and history tiers."""
        engine = self.server_instance  # type: ignore[attr-defined]
        db_path = engine.engine_root / "current" / slug / "session.db"
        if db_path.exists():
            return db_path
        db_path = engine.engine_root / "last_run" / slug / "session.db"
        if db_path.exists():
            return db_path
        hist_dir = engine.engine_root / "history" / slug
        if hist_dir.exists():
            runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
            if runs:
                cand = runs[-1] / "session.db"
                if cand.exists():
                    return cand
        return None

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
        live_statuses = {s["project"]: s for s in live_workers}

        # 2. Auto-archive any inactive sessions lingering in current/
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
                            logger.info("Auto-archiving inactive session in current/: %s", slug)
                            rotator.archive_project(slug)
                        except Exception as exc:
                            logger.warning("Could not auto-archive %s: %s", slug, exc)

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

            item = {
                "name": slug,
                "is_active": is_active,
                "status": "INTERRUPTED" if is_interrupted else ("ACTIVE" if is_active else "STOPPED"),
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
        debounce_input = body.get("debounce") if "debounce" in body else body.get("debounce_window")
        if debounce_input is not None:
            try:
                debounce = float(debounce_input)
            except (ValueError, TypeError):
                debounce = 3.5
        else:
            meta = read_project_meta(self.server_instance.engine_root, _slug(name))
            saved_db = meta.get("debounce_window") or meta.get("debounce")
            if saved_db:
                try:
                    debounce = float(saved_db)
                except (ValueError, TypeError):
                    debounce = 3.5
            else:
                debounce = 3.5
        track_reads = bool(body.get("track_reads", True))
        track_exec = bool(body.get("track_exec", True))
        shadow_git = bool(body.get("shadow_git", True))
        git_init_primary = bool(body.get("git_init_primary", False))
        ide_profile = str(body.get("ide_profile", "antigravity")).strip() or "antigravity"
        ide_custom_marker = str(body.get("ide_custom_marker", "")).strip() or None

        if not name:
            self._send_error("Parameter 'project' is required.")  # type: ignore[attr-defined]
            return
        if not path:
            self._send_error("Parameter 'path' is required.")  # type: ignore[attr-defined]
            return

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
                res = {"stopped": False, "note": "Worker process not found or already stopped."}
            slug = _slug(name)
            for tier in ("current", "last_run"):
                lock_file = self.server_instance.engine_root / tier / slug / "session.lock"  # type: ignore[attr-defined]
                if lock_file.exists():
                    try:
                        lock_file.unlink()
                    except Exception:
                        pass
            update_project_meta(self.server_instance.engine_root, name, clean_exit="true", status="stopped")  # type: ignore[attr-defined]
            self._send_json({"status": "stopped", "result": res})  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Failed to stop project via API")
            self._send_error(str(exc), status=400)  # type: ignore[attr-defined]

    def _handle_api_project_specs(self, project_name: str) -> None:
        """Return project specs, powers, debounce window, and GitHub remote info."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        meta = read_project_meta(engine.engine_root, slug)

        target_path = self._resolve_target_path(slug)
        target_path_str = str(target_path) if target_path else meta.get("target_path", "")

        debounce = 3.5
        if meta.get("debounce_window") or meta.get("debounce"):
            try:
                debounce = float(meta.get("debounce_window") or meta.get("debounce"))
            except (ValueError, TypeError):
                pass

        powers = {"track_edits": True, "track_reads": True, "track_exec": True, "shadow_git": True}
        if meta.get("powers"):
            try:
                p_meta = json.loads(meta["powers"]) if isinstance(meta["powers"], str) else meta["powers"]
                if isinstance(p_meta, dict):
                    powers.update(p_meta)
            except Exception:
                pass

        ide_profile = meta.get("ide_profile", "antigravity")
        ide_custom_marker = meta.get("ide_custom_marker", "")

        # Check live supervisor status
        live = engine.supervisor.get_status()
        for w in live:
            if w.get("project") == slug and w.get("alive"):
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
                        s_data = json.loads(sf.read_text(encoding="utf-8"))
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
                    except Exception:
                        pass

        self._send_json({  # type: ignore[attr-defined]
            "project": slug,
            "target_path": target_path_str,
            "debounce": debounce,
            "debounce_window": debounce,
            "monitored_ai_env": meta.get("monitored_ai_env", "Google Antigravity"),
            "powers": powers,
            "ide_profile": ide_profile,
            "ide_custom_marker": ide_custom_marker,
            "remote_url": meta.get("remote_url", ""),
            "remote_branch": meta.get("remote_branch", "main"),
            "last_push_status": meta.get("last_push_status", "Never pushed"),
            "last_push_time": meta.get("last_push_time", ""),
            "last_push_commit": meta.get("last_push_commit", ""),
        })
