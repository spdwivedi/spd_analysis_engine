"""
scripts/storage/meta.py
=======================
Project metadata read/write helpers for the storage package.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils import _slug, _utcnow_iso

def suppress_file_path(path: Any, duration_s: float = 5.0) -> None:
    try:
        from ..event_bundler import suppress_file_path as _sfp
        _sfp(path, duration_s=duration_s)
    except Exception:
        pass

logger = logging.getLogger(__name__)

try:
    from ..core.project_config import DEFAULT_DEBOUNCE_S, DEFAULT_MAX_BURST_S
except Exception:
    try:
        from spd_analysis_engine.scripts.core.project_config import DEFAULT_DEBOUNCE_S, DEFAULT_MAX_BURST_S
    except Exception:
        DEFAULT_DEBOUNCE_S, DEFAULT_MAX_BURST_S = 3.5, 60.0

PROFILE_MAP = {
    "antigravity": "Google Antigravity",
    "cursor": "Cursor AI",
    "windsurf": "Windsurf / Codeium",
    "claude_code": "Claude Code CLI",
    "custom": "Custom AI",
}
REV_PROFILE_MAP = {v.lower(): k for k, v in PROFILE_MAP.items()}


def _apply_meta_updates(data: dict[str, str], updates: dict[str, Any]) -> dict[str, str]:
    """Helper to apply spec & debounce updates without clobbering existing custom values."""
    kwargs = dict(updates)
    explicit_debounce = bool(kwargs.pop("explicit_debounce", False) or kwargs.pop("force_debounce", False))

    # Resolve target_path if passed
    if "target_path" in kwargs and kwargs["target_path"]:
        kwargs["target_path"] = str(kwargs["target_path"]).strip()

    # Detect existing custom debounce
    existing_db = data.get("debounce_window") or data.get("debounce")
    existing_is_custom = False
    if existing_db:
        try:
            if float(existing_db) != DEFAULT_DEBOUNCE_S:
                existing_is_custom = True
        except (ValueError, TypeError):
            pass

    # Debounce handling
    db_candidate = kwargs.pop("debounce_window", None) or kwargs.pop("debounce", None)
    if db_candidate is not None:
        try:
            db_val = float(db_candidate)
            if existing_is_custom and db_val == DEFAULT_DEBOUNCE_S and not explicit_debounce:
                pass
            else:
                data["debounce_window"] = str(db_val)
                data["debounce"] = str(db_val)
        except (ValueError, TypeError):
            data["debounce_window"] = str(db_candidate).strip()
            data["debounce"] = str(db_candidate).strip()
    elif existing_db:
        data["debounce_window"] = str(existing_db).strip()
        data["debounce"] = str(existing_db).strip()

    # Monitored AI env mapping
    if "monitored_ai_env" in kwargs and kwargs["monitored_ai_env"]:
        data["monitored_ai_env"] = str(kwargs.pop("monitored_ai_env")).strip()
    elif "ide_profile" in kwargs and kwargs["ide_profile"] and "monitored_ai_env" not in data:
        prof = str(kwargs["ide_profile"]).strip()
        data["monitored_ai_env"] = PROFILE_MAP.get(prof, prof)

    # Powers dictionary / JSON serialization
    if "powers" in kwargs:
        p_val = kwargs.pop("powers")
        if p_val is not None:
            if isinstance(p_val, (dict, list)):
                data["powers"] = json.dumps(p_val)
            else:
                data["powers"] = str(p_val).strip()

    for k, v in kwargs.items():
        if v is not None:
            if isinstance(v, (dict, list)):
                data[str(k).strip()] = json.dumps(v)
            else:
                data[str(k).strip()] = str(v).strip()

    return data


def synthesize_project_meta(engine_root: Path | str, project_name: str, persist: bool = True) -> dict[str, Any] | None:
    """
    Self-healing metadata reconstructor for inactive/resumed projects.
    Reconstructs project.meta by inspecting session.db across current/last_run/history tiers,
    checking worker.status.json, or directory baselines.
    When persist=True, saves recovered metadata to current/<slug>/project.meta.
    Returns None only if zero metadata, database records, or directory baselines exist.
    """
    slug = _slug(project_name)
    engine_p = Path(engine_root).resolve()

    recovered_target_path: str | None = None
    recovered_debounce: float | None = None
    recovered_powers: dict[str, Any] | None = None
    recovered_ide_profile: str = "antigravity"
    recovered_custom_marker: str | None = None

    # 1. Check if session.db exists in current, last_run, or history runs
    db_candidates = [
        engine_p / "current" / slug / "session.db",
        engine_p / "last_run" / slug / "session.db",
    ]
    hist_dir = engine_p / "history" / slug
    if hist_dir.exists():
        runs = sorted((d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_")), reverse=True)
        for r in runs:
            db_candidates.append(r / "session.db")

    for db_file in db_candidates:
        if db_file.exists():
            try:
                db_uri = Path(db_file).resolve().as_uri() + "?mode=ro"
                conn = sqlite3.connect(db_uri, uri=True, timeout=5.0)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = ON;")
                conn.execute("PRAGMA wal_autocheckpoint = 0;")
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
                if cur.fetchone():
                    cur.execute("PRAGMA table_info(sessions)")
                    cols = {row["name"] for row in cur.fetchall()}
                    cur.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 1")
                    s_row = cur.fetchone()
                    if s_row:
                        if "target_path" in cols and s_row["target_path"]:
                            recovered_target_path = str(s_row["target_path"]).strip()
                        if "debounce_window" in cols and s_row["debounce_window"]:
                            try:
                                recovered_debounce = float(s_row["debounce_window"])
                            except (ValueError, TypeError):
                                pass
                        if "powers" in cols and s_row["powers"]:
                            try:
                                recovered_powers = json.loads(s_row["powers"]) if isinstance(s_row["powers"], str) else s_row["powers"]
                                if isinstance(recovered_powers, dict):
                                    if "ide_profile" in recovered_powers:
                                        recovered_ide_profile = str(recovered_powers["ide_profile"]).strip()
                                    if "ide_custom_marker" in recovered_powers:
                                        recovered_custom_marker = str(recovered_powers["ide_custom_marker"]).strip()
                            except Exception:
                                pass
                        if "ide_profile" in cols and s_row["ide_profile"]:
                            recovered_ide_profile = str(s_row["ide_profile"]).strip()
                        if "ide_custom_marker" in cols and s_row["ide_custom_marker"]:
                            recovered_custom_marker = str(s_row["ide_custom_marker"]).strip()
                conn.close()
                if recovered_target_path:
                    break
            except Exception as exc:
                logger.debug("Error probing %s for metadata synthesis: %s", db_file, exc)

    # 2. Inspect history/<slug>/ for the most recent valid project.meta if target_path still unknown
    if not recovered_target_path and hist_dir.exists():
        runs = sorted((d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_")), reverse=True)
        for r in runs:
            hist_meta = r / "project.meta"
            if hist_meta.exists():
                try:
                    for line in hist_meta.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.startswith("target_path="):
                            cand_path = line.split("=", 1)[1].strip()
                            if cand_path:
                                recovered_target_path = cand_path
                                break
                    if recovered_target_path:
                        break
                except Exception:
                    pass

    # 3. Inspect worker.status.json in current or last_run
    if not recovered_target_path:
        for tier in ("current", "last_run"):
            status_file = engine_p / tier / slug / "worker.status.json"
            if status_file.exists():
                try:
                    with open(status_file, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                    if s_data.get("target_path"):
                        recovered_target_path = str(s_data["target_path"]).strip()
                    if s_data.get("powers") and isinstance(s_data["powers"], dict):
                        recovered_powers = s_data["powers"]
                        if "ide_profile" in recovered_powers:
                            recovered_ide_profile = recovered_powers["ide_profile"]
                        if "ide_custom_marker" in recovered_powers:
                            recovered_custom_marker = recovered_powers["ide_custom_marker"]
                    if not recovered_debounce:
                        db_info = s_data.get("debounce", {})
                        if isinstance(db_info, dict) and "quiet_period" in db_info:
                            recovered_debounce = float(db_info["quiet_period"])
                    if recovered_target_path:
                        break
                except Exception:
                    pass

    if not recovered_target_path:
        return None

    reconstructed_meta: dict[str, Any] = {
        "project_name": slug,
        "target_path": recovered_target_path,
        "debounce_window": recovered_debounce or DEFAULT_DEBOUNCE_S,
        "max_burst_s": DEFAULT_MAX_BURST_S,
        "powers": recovered_powers or {"track_edits": True, "track_reads": True, "track_exec": True, "shadow_git": True},
        "ide_profile": recovered_ide_profile,
        "resumed_at": datetime.now(timezone.utc).isoformat(),
    }
    if recovered_custom_marker:
        reconstructed_meta["ide_custom_marker"] = recovered_custom_marker

    if persist:
        # Save this metadata to current/<slug>/project.meta
        target_meta_file = engine_p / "current" / slug / "project.meta"
        target_meta_file.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for k, v in reconstructed_meta.items():
            if isinstance(v, (dict, list)):
                v_str = json.dumps(v)
            else:
                v_str = str(v)
            lines.append(f"{k}={v_str}")
        suppress_file_path(str(target_meta_file), duration_s=5.0)
        target_meta_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        suppress_file_path(str(target_meta_file), duration_s=5.0)
        logger.info("Self-healed and reconstructed project.meta for '%s'", slug)

    return reconstructed_meta


def read_project_meta(engine_root: Path | str, project_name: str, auto_synthesize: bool = False) -> dict[str, str]:
    """Read key=value pairs from current, last_run, or history project.meta."""
    slug = _slug(project_name)
    engine_p = Path(engine_root).resolve()
    data: dict[str, str] = {}
    found = False
    for tier in ("current", "last_run"):
        meta_file = engine_p / tier / slug / "project.meta"
        if meta_file.exists():
            for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip()
            found = True
            break
    if not found:
        hist_dir = engine_p / "history" / slug
        if hist_dir.exists():
            runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
            if runs:
                meta_file = runs[-1] / "project.meta"
                if meta_file.exists():
                    for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            data[k.strip()] = v.strip()
    if not found:
        durable_cfg = engine_p / "data" / "projects" / slug / "project.config.json"
        if durable_cfg.is_file():
            try:
                cfg = json.loads(durable_cfg.read_text(encoding="utf-8"))
                for k, v in cfg.items():
                    data[k] = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                found = True
            except Exception:
                pass

    if not found and auto_synthesize:
        # Self-healing fallback: synthesize metadata in-memory without writing to disk
        synth = synthesize_project_meta(engine_p, slug, persist=False)
        if synth and synth.get("target_path"):
            for k, v in synth.items():
                data[k] = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            found = True

    # Synchronize debounce and debounce_window
    if data.get("debounce_window") and not data.get("debounce"):
        data["debounce"] = data["debounce_window"]
    elif data.get("debounce") and not data.get("debounce_window"):
        data["debounce_window"] = data["debounce"]

    # Synchronize ide_profile and monitored_ai_env
    if data.get("ide_profile") and not data.get("monitored_ai_env"):
        data["monitored_ai_env"] = PROFILE_MAP.get(data["ide_profile"], data["ide_profile"])
    elif data.get("monitored_ai_env") and not data.get("ide_profile"):
        data["ide_profile"] = REV_PROFILE_MAP.get(data["monitored_ai_env"].lower(), "antigravity")

    # Auto-discover remote origin URL and branch from native .git if not configured
    if data.get("target_path") and (not data.get("remote_url") or data.get("remote_url") in ("Not Linked", "none", "")):
        try:
            from spd_analysis_engine.scripts.git_shadow import discover_native_git_remote
            disc_url, disc_branch = discover_native_git_remote(data["target_path"])
            if disc_url:
                data["remote_url"] = disc_url
                if not data.get("remote_branch"):
                    data["remote_branch"] = disc_branch
        except Exception:
            pass

    return data


def update_project_meta(engine_root: Path | str, project_name: str, **kwargs: Any) -> None:
    """Update or append key=value pairs in current and last_run project.meta."""
    slug = _slug(project_name)
    engine_p = Path(engine_root).resolve()
    updated = False
    for tier in ("current", "last_run"):
        p_dir = engine_p / tier / slug
        if p_dir.exists():
            meta_file = p_dir / "project.meta"
            data: dict[str, str] = {}
            if meta_file.exists():
                for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        data[k.strip()] = v.strip()
            data = _apply_meta_updates(data, kwargs)
            lines = [f"{k}={v}" for k, v in data.items()]
            suppress_file_path(str(meta_file), duration_s=5.0)
            meta_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            suppress_file_path(str(meta_file), duration_s=5.0)
            updated = True
    if not updated:
        p_dir = engine_p / "last_run" / slug
        p_dir.mkdir(parents=True, exist_ok=True)
        meta_file = p_dir / "project.meta"
        data = {}
        data = _apply_meta_updates(data, kwargs)
        lines = [f"{k}={v}" for k, v in data.items()]
        suppress_file_path(str(meta_file), duration_s=5.0)
        meta_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        suppress_file_path(str(meta_file), duration_s=5.0)


def resolve_project_context(project_name: str, engine_root: Path | str, auto_synthesize: bool = False) -> dict[str, Any]:
    """
    Resolve project context across current, last_run, and history storage tiers.

    Returns dict with keys:
        slug: str
        tier: str ('current', 'last_run', 'history', or 'unknown')
        target_path: Path | None
        db_path: Path | None
        shadow_git_path: Path | None
        dir_path: Path | None
        meta: dict[str, str]
    """
    slug = _slug(project_name)
    engine_p = Path(engine_root).resolve()

    durable_dir = engine_p / "data" / "projects" / slug
    curr_dir = engine_p / "current" / slug
    last_run_dir = engine_p / "last_run" / slug
    hist_dir = engine_p / "history" / slug

    chosen_tier = "unknown"
    chosen_dir: Path | None = None
    chosen_db: Path | None = None

    if durable_dir.exists() and (durable_dir / "session.db").exists():
        chosen_tier = "durable"
        chosen_dir = durable_dir
        chosen_db = durable_dir / "session.db"
    elif curr_dir.exists() and (curr_dir / "session.db").exists():
        chosen_tier = "current"
        chosen_dir = curr_dir
        chosen_db = curr_dir / "session.db"
    elif last_run_dir.exists() and (last_run_dir / "session.db").exists():
        chosen_tier = "last_run"
        chosen_dir = last_run_dir
        chosen_db = last_run_dir / "session.db"
    elif durable_dir.exists():
        chosen_tier = "durable"
        chosen_dir = durable_dir
        if (durable_dir / "session.db").exists():
            chosen_db = durable_dir / "session.db"
    elif curr_dir.exists():
        chosen_tier = "current"
        chosen_dir = curr_dir
        if (curr_dir / "session.db").exists():
            chosen_db = curr_dir / "session.db"
    elif last_run_dir.exists():
        chosen_tier = "last_run"
        chosen_dir = last_run_dir
        if (last_run_dir / "session.db").exists():
            chosen_db = last_run_dir / "session.db"
    elif hist_dir.exists():
        runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
        if runs:
            chosen_tier = "history"
            chosen_dir = runs[-1]
            if (runs[-1] / "session.db").exists():
                chosen_db = runs[-1] / "session.db"

    meta = read_project_meta(engine_p, slug, auto_synthesize=auto_synthesize)

    resolved_target: Path | None = None
    if meta.get("target_path"):
        cand = Path(meta["target_path"])
        if cand.exists() or resolved_target is None:
            resolved_target = cand

    if (resolved_target is None or not resolved_target.exists()) and chosen_db and chosen_db.exists():
        try:
            db_uri = Path(chosen_db).resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(db_uri, uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON;")
            conn.execute("PRAGMA wal_autocheckpoint = 0;")
            cur = conn.cursor()
            cur.execute("SELECT target_path FROM sessions WHERE target_path IS NOT NULL AND target_path != '' ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row and row["target_path"]:
                cand = Path(row["target_path"])
                if cand.exists() or resolved_target is None:
                    resolved_target = cand
            conn.close()
        except Exception:
            pass

    if resolved_target is None or not resolved_target.exists():
        for cand_dir in (curr_dir, last_run_dir):
            status_f = cand_dir / "worker.status.json"
            if status_f.exists():
                try:
                    with open(status_f, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                    if s_data.get("target_path"):
                        cand = Path(s_data["target_path"])
                        if cand.exists() or resolved_target is None:
                            resolved_target = cand
                            break
                except Exception:
                    pass

    resolved_shadow: Path | None = None
    for cand_dir in (chosen_dir, durable_dir, curr_dir, last_run_dir):
        if cand_dir and (cand_dir / "shadow_git").exists():
            resolved_shadow = cand_dir / "shadow_git"
            break

    if chosen_db is None:
        if (durable_dir / "session.db").exists():
            chosen_db = durable_dir / "session.db"
        elif (curr_dir / "session.db").exists():
            chosen_db = curr_dir / "session.db"
        elif (last_run_dir / "session.db").exists():
            chosen_db = last_run_dir / "session.db"

    return {
        "slug": slug,
        "tier": chosen_tier,
        "target_path": resolved_target,
        "db_path": chosen_db,
        "shadow_git_path": resolved_shadow,
        "dir_path": chosen_dir,
        "meta": meta,
    }

