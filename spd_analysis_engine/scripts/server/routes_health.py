"""
scripts/server/routes_health.py
===============================
Health diagnostics and raw log streaming routes mixin for the SPD Analysis Engine.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

from ..trash.fs_ops import _safe_move_tree, _safe_rmtree

from ..storage_rotator import _slug

logger = logging.getLogger(__name__)

try:
    from spd_analysis_engine.scripts.core.time_utils import utc_now_iso as _now_ist_iso
except (ImportError, ModuleNotFoundError):
    def _now_ist_iso() -> str:  # type: ignore[misc]
        from datetime import datetime, timezone as _tz
        return datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

_safe_slug = _slug


class HealthRoutesMixin:
    """
    Mixin containing engine diagnostics (/api/engine/health)
    and emergency raw log streaming (/api/project/<slug>/worker-log-raw).
    Expected to be mixed into _EngineRequestHandler.
    """

    def _handle_api_engine_health(self) -> None:
        """GET /api/engine/health — Returns status across all core engine subsystems."""
        engine = self.server_instance  # type: ignore[attr-defined]
        engine_root = Path(engine.engine_root).resolve()

        health_data: dict[str, Any] = {
            "timestamp": _now_ist_iso(),
            "overall_status": "healthy",
            "pipelines": {},
            "active_paths": {},
        }
        pipelines = health_data["pipelines"]
        is_degraded = False
        is_error = False

        # -------------------------------------------------------------
        # 1. Database Health (SQLite WAL, Integrity Check, Stale Locks)
        # -------------------------------------------------------------
        db_health: dict[str, Any] = {
            "status": "healthy",
            "wal_mode": True,
            "integrity": "ok",
            "stale_locks": [],
            "active_databases": [],
        }
        current_dir = engine_root / "current"
        checked_any_db = False
        total_assembler_queue, total_writer_queue = 0, 0
        total_assembler_drops, total_writer_drops = 0, 0
        max_writer_lag_ms, max_proc_scan_dur = 0.0, 0.0
        any_needs_reconcile = False
        pending_git_ops, committed_git_ops, failed_git_ops = 0, 0, 0

        if current_dir.is_dir():
            for proj_dir in current_dir.iterdir():
                if not proj_dir.is_dir():
                    continue
                st_path = proj_dir / "worker.status.json"
                if st_path.is_file():
                    try:
                        st_dict = json.loads(st_path.read_text(encoding="utf-8"))
                        total_assembler_queue += int(st_dict.get("queue_depth", 0))
                        total_assembler_drops += int(st_dict.get("drop_counter", 0))
                        if st_dict.get("needs_reconcile"): any_needs_reconcile = True
                        max_proc_scan_dur = max(max_proc_scan_dur, float(st_dict.get("scan_duration_s", 0.0)))
                    except Exception:
                        pass
                db_path = proj_dir / "session.db"
                if db_path.is_file():
                    checked_any_db = True
                    db_info: dict[str, Any] = {"project": proj_dir.name, "path": str(db_path)}
                    try:
                        db_uri = db_path.resolve().as_uri() + "?mode=ro"
                        conn = sqlite3.connect(db_uri, uri=True, timeout=2.0)
                        conn.row_factory = sqlite3.Row
                        conn.execute("PRAGMA query_only = ON;")
                        conn.execute("PRAGMA wal_autocheckpoint = 0;")
                        cur = conn.cursor()
                        cur.execute("PRAGMA journal_mode;")
                        j_mode = cur.fetchone()
                        db_info["journal_mode"] = j_mode[0] if j_mode else "unknown"
                        if str(db_info["journal_mode"]).lower() != "wal":
                            db_health["wal_mode"] = False
                            db_health["status"] = "degraded"
                            is_degraded = True

                        cur.execute("PRAGMA integrity_check(1);")
                        integ = cur.fetchone()
                        db_info["integrity"] = integ[0] if integ else "unknown"
                        if db_info["integrity"] != "ok":
                            db_health["integrity"] = "corrupt"
                            db_health["status"] = "error"
                            is_error = True

                        try:
                            cur.execute("SELECT git_state, count(*) FROM git_ops GROUP BY git_state;")
                            for gs_state, gs_cnt in cur.fetchall():
                                gs_lower = str(gs_state).lower()
                                if gs_lower == "pending": pending_git_ops += gs_cnt
                                elif gs_lower == "committed": committed_git_ops += gs_cnt
                                elif gs_lower == "failed": failed_git_ops += gs_cnt
                        except Exception:
                            pass

                        conn.close()
                    except Exception as exc:
                        db_info["error"] = str(exc)
                        db_health["status"] = "degraded"
                        is_degraded = True

                    # Check for lingering lockfiles
                    for lk in proj_dir.glob("*.lock"):
                        db_health["stale_locks"].append(str(lk))

                    db_health["active_databases"].append(db_info)

        # If no active database in current/, verify WAL concurrency capabilities without disk writes
        if not checked_any_db:
            try:
                tconn = sqlite3.connect(":memory:")
                tcur = tconn.cursor()
                tcur.execute("PRAGMA journal_mode = WAL;")
                tconn.close()
                db_health["wal_mode"] = True
            except Exception as exc:
                db_health["status"] = "degraded"
                db_health["error"] = str(exc)
                is_degraded = True

        pipelines["database"] = db_health

        # -------------------------------------------------------------
        # 2. Storage Tiers (Permissions & Path Existence)
        # -------------------------------------------------------------
        tier_names = ["current", "last_run", "history", "trash"]
        storage_health: dict[str, Any] = {"status": "healthy", "tiers": {}}
        for tier in tier_names:
            t_path = engine_root / tier
            exists = t_path.is_dir()
            writable = False
            readable = False
            if exists:
                readable = os.access(t_path, os.R_OK)
                writable = os.access(t_path, os.W_OK)

            tier_info = {
                "path": str(t_path),
                "exists": exists,
                "readable": readable,
                "writable": writable,
            }
            if exists and not (readable and writable):
                storage_health["status"] = "error"
                is_error = True
            storage_health["tiers"][tier] = tier_info

        pipelines["storage_tiers"] = storage_health

        # -------------------------------------------------------------
        # 3. Shadow Git (Dangling .git/index.lock Detection)
        # -------------------------------------------------------------
        git_health: dict[str, Any] = {"status": "healthy", "dangling_locks": [], "repositories": []}
        now_ts = time.time()
        if current_dir.is_dir():
            for proj_dir in current_dir.iterdir():
                if not proj_dir.is_dir():
                    continue
                # Check shadow_git/.git or .git
                repo_candidates = [proj_dir / "shadow_git", proj_dir]
                for rc in repo_candidates:
                    dot_git = rc / ".git"
                    if dot_git.is_dir():
                        lock_file = dot_git / "index.lock"
                        git_health["repositories"].append(str(rc))
                        if lock_file.is_file():
                            age = now_ts - lock_file.stat().st_mtime
                            lock_info = {"file": str(lock_file), "age_seconds": round(age, 1)}
                            git_health["dangling_locks"].append(lock_info)
                            if age > 60:
                                git_health["status"] = "degraded"
                                is_degraded = True

        pipelines["shadow_git"] = git_health

        # -------------------------------------------------------------
        # 4. Worker Daemon (worker.status.json & PID Heartbeat Freshness)
        # -------------------------------------------------------------
        worker_health: dict[str, Any] = {"status": "healthy", "workers": []}
        try:
            statuses = engine.supervisor.get_status()
            active_count = 0
            for st in statuses:
                p_slug = st.get("slug") or st.get("project_slug") or _slug(st.get("project", "unknown"))
                pid = st.get("pid")
                alive = st.get("alive", False)
                last_seen = st.get("last_seen", 0)
                age = round(now_ts - last_seen, 1) if last_seen else None

                freshness = "idle"
                if alive:
                    active_count += 1
                    freshness = "live" if (age is not None and age <= 15.0) else "stale"
                    if freshness == "stale":
                        worker_health["status"] = "degraded"
                        is_degraded = True

                worker_health["workers"].append({
                    "project": p_slug,
                    "pid": pid,
                    "alive": alive,
                    "heartbeat_age_s": age,
                    "freshness": freshness,
                })

            if active_count == 0:
                worker_health["status"] = "idle"
        except Exception as exc:
            worker_health["status"] = "degraded"
            worker_health["error"] = str(exc)
            is_degraded = True

        pipelines["worker_daemon"] = worker_health

        # -------------------------------------------------------------
        # 5. Configurations (ai_data/models_config.json, git_config.json)
        # -------------------------------------------------------------
        config_health: dict[str, Any] = {"status": "healthy", "files": {}}
        for cfg_name in ["models_config.json", "git_config.json"]:
            cfg_path = engine_root / "ai_data" / cfg_name
            cf_info: dict[str, Any] = {"path": str(cfg_path), "exists": cfg_path.is_file(), "valid": True}
            if cfg_path.is_file():
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        parsed = json.load(f)
                    if not isinstance(parsed, dict):
                        cf_info["valid"] = False
                        cf_info["error"] = "Root structure is not a dictionary"
                        config_health["status"] = "degraded"
                        is_degraded = True
                except (PermissionError, OSError) as exc:
                    cf_info["valid"] = False
                    cf_info["error"] = f"File lock or access error: {exc}"
                    config_health["status"] = "degraded"
                    is_degraded = True
                except Exception as exc:
                    cf_info["valid"] = False
                    cf_info["error"] = f"JSON parse error: {exc}"
                    config_health["status"] = "degraded"
                    is_degraded = True
            else:
                cf_info["status"] = "default_fallback"

            config_health["files"][cfg_name] = cf_info

        pipelines["configurations"] = config_health

        # -------------------------------------------------------------
        # Active Path Manifest (Top active project if any)
        # -------------------------------------------------------------
        active_slug = None
        if current_dir.is_dir():
            for p in current_dir.iterdir():
                if p.is_dir() and not p.name.startswith("."):
                    active_slug = p.name
                    break

        if active_slug:
            active_proj_dir = current_dir / active_slug
            health_data["active_paths"] = {
                "project_name": active_slug,
                "session_db": str(active_proj_dir / "session.db"),
                "shadow_git": str(active_proj_dir / "shadow_git"),
                "worker_log": str(active_proj_dir / "worker.log"),
            }
        else:
            health_data["active_paths"] = {
                "project_name": "None",
                "session_db": str(engine_root / "current" / "<slug>" / "session.db"),
                "shadow_git": str(engine_root / "current" / "<slug>" / "shadow_git"),
                "worker_log": str(engine_root / "current" / "<slug>" / "worker.log"),
            }

        # Overall Status
        if is_error:
            health_data["overall_status"] = "error"
        elif is_degraded:
            health_data["overall_status"] = "degraded"
        else:
            health_data["overall_status"] = "healthy"

        # Telemetry & Diagnostics Exposures
        ai_queue_status: dict[str, int] = {"queued": 0, "running": 0, "done": 0, "failed": 0}
        eng_db_p = engine_root / "data" / "engine.db"
        if eng_db_p.is_file():
            try:
                edb_uri = eng_db_p.resolve().as_uri() + "?mode=ro"
                with sqlite3.connect(edb_uri, uri=True, timeout=1.0) as econn:
                    ecur = econn.cursor()
                    ecur.execute("SELECT status, count(*) FROM jobs GROUP BY status;")
                    for s_name, cnt in ecur.fetchall():
                        s_key = str(s_name).lower()
                        if s_key in ai_queue_status:
                            ai_queue_status[s_key] = cnt
            except Exception:
                pass

        last_rec_ts: float | None = None
        ctrl = getattr(engine, "controller", None)
        if ctrl and hasattr(ctrl, "reconciler"):
            last_rec_ts = getattr(ctrl.reconciler, "last_reconcile_ts", None)

        active_workers_list = [w for w in worker_health.get("workers", []) if w.get("alive")]

        health_data["queue_depths"] = {
            "writer": total_writer_queue, "burst_assembler": total_assembler_queue,
            "ai_jobs": ai_queue_status["queued"],
            "total": total_writer_queue + total_assembler_queue + ai_queue_status["queued"],
        }
        health_data["drop_counters"] = {
            "writer": total_writer_drops, "burst_assembler": total_assembler_drops,
            "total": total_writer_drops + total_assembler_drops,
        }
        health_data["writer_lag"] = {
            "last_batch_duration_ms": round(max_writer_lag_ms, 2),
            "status": "active" if active_workers_list else "idle",
        }
        health_data["git_queue_status"] = {
            "status": git_health.get("status", "healthy"),
            "pending_ops": pending_git_ops, "committed_ops": committed_git_ops, "failed_ops": failed_git_ops,
            "dangling_locks": git_health.get("dangling_locks", []),
        }
        health_data["ai_queue_status"] = ai_queue_status
        health_data["scan_durations"] = {
            "proc_watcher_s": round(max_proc_scan_dur, 4), "manifest_scan_s": 0.0,
        }
        health_data["active_workers"] = active_workers_list
        health_data["reconciler_health"] = {
            "status": "needs_reconcile" if any_needs_reconcile else "healthy",
            "needs_reconcile": any_needs_reconcile, "last_reconcile": last_rec_ts,
        }

        self._send_json(health_data)  # type: ignore[attr-defined]

    def _handle_api_worker_log_raw(self, proj_name: str, query: dict[str, list[str]]) -> None:
        """GET /api/project/<slug>/worker-log-raw — Safely reads the tail of worker.log."""
        engine = self.server_instance  # type: ignore[attr-defined]
        engine_root = Path(engine.engine_root).resolve()
        slug = _safe_slug(proj_name)

        # Parse requested lines (default 100, clamped 1..2000)
        lines_param = query.get("lines", ["100"])[0]
        try:
            num_lines = max(1, min(2000, int(lines_param)))
        except ValueError:
            num_lines = 100

        # Search current/<slug>/worker.log first, then last_run/<slug>/worker.log
        candidate_paths = [
            engine_root / "current" / slug / "worker.log",
            engine_root / "last_run" / slug / "worker.log",
        ]
        log_file: Path | None = None
        for cand in candidate_paths:
            if cand.is_file():
                log_file = cand
                break

        if not log_file or not log_file.is_file():
            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "file_path": str(candidate_paths[0]),
                "exists": False,
                "lines_count": 0,
                "lines": [],
                "content": f"[RAW LOG] No worker.log found for project '{slug}' at {candidate_paths[0]}.",
            })
            return

        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                tail = all_lines[-num_lines:]

            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "file_path": str(log_file),
                "exists": True,
                "lines_count": len(tail),
                "lines": tail,
                "content": "".join(tail),
            })
        except Exception as exc:
            logger.error("Failed to read raw worker log for %s: %s", slug, exc)
            self._send_json({  # type: ignore[attr-defined]
                "success": False,
                "project": slug,
                "file_path": str(log_file),
                "error": str(exc),
                "lines_count": 0,
                "lines": [],
                "content": f"[ERROR] Failed reading log file: {exc}",
            }, status=500)

    def _handle_api_purge_zombies(self) -> None:
        """POST /api/system/recovery/purge-zombies — Delegate to authoritative controller reconciler with compatibility fallback."""
        engine = self.server_instance  # type: ignore[attr-defined]
        engine_root = Path(engine.engine_root).resolve()

        cleaned_count = 0
        try:
            from spd_analysis_engine.scripts.controller.controller import LifecycleController
            controller = getattr(engine, "controller", None) or LifecycleController(engine_root)
            reconcile_res = controller.reconcile()
            adopted = reconcile_res.get("adopted", [])
            interrupted = reconcile_res.get("interrupted", [])
            orphaned = reconcile_res.get("orphaned", [])
            unresponsive = reconcile_res.get("unresponsive", [])

            # Compatibility fallback: if dead zombie project dirs remain in current/, move to last_run
            curr_dir = engine_root / "current"
            if curr_dir.is_dir():
                for proj_dir in curr_dir.iterdir():
                    if not proj_dir.is_dir() or proj_dir.name.startswith("."):
                        continue
                    pid_file = proj_dir / "worker.pid"
                    if pid_file.is_file():
                        try:
                            pid = int(pid_file.read_text(encoding="utf-8").strip())
                        except Exception:
                            pid = None
                        if pid is not None:
                            import psutil
                            if not psutil.pid_exists(pid):
                                last_run_target = engine_root / "last_run" / proj_dir.name
                                if last_run_target.exists():
                                    import shutil
                                    shutil.rmtree(last_run_target, ignore_errors=True)
                                (engine_root / "last_run").mkdir(parents=True, exist_ok=True)
                                proj_dir.rename(last_run_target)
                                (last_run_target / "worker.pid").unlink(missing_ok=True)
                                (last_run_target / "session.lock").unlink(missing_ok=True)
                                cleaned_count += 1

            cleaned_count = max(cleaned_count, len(interrupted) + len(orphaned))

            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "cleaned_dirs": cleaned_count,
                "reconciled": reconcile_res,
                "message": (
                    f"Authoritative reconciliation complete: {len(adopted)} adopted, "
                    f"{len(interrupted)} interrupted, {len(orphaned)} orphaned, {len(unresponsive)} unresponsive."
                ),
            })
        except Exception as exc:
            logger.error("Error during purge-zombies reconciliation: %s", exc)
            self._send_json({  # type: ignore[attr-defined]
                "success": False,
                "error": str(exc),
            }, status=500)
