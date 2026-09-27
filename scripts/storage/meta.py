"""
scripts/storage/meta.py
=======================
Project metadata read/write helpers for the storage package.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    from .utils import _slug, _utcnow_iso  # noqa: F401  (_utcnow_iso imported for consistency)
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.storage.utils import _slug, _utcnow_iso  # noqa: F401
    except (ImportError, ModuleNotFoundError):
        from scripts.storage.utils import _slug, _utcnow_iso  # noqa: F401

try:
    from scripts.event_bundler import suppress_file_path
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.event_bundler import suppress_file_path
    except (ImportError, ModuleNotFoundError):
        def suppress_file_path(path: Any, duration_s: float = 5.0) -> None:
            pass

logger = logging.getLogger(__name__)


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
            if float(existing_db) != 3.5:
                existing_is_custom = True
        except (ValueError, TypeError):
            pass

    # Debounce handling
    db_candidate = None
    if "debounce_window" in kwargs:
        db_candidate = kwargs.pop("debounce_window")
    elif "debounce" in kwargs:
        db_candidate = kwargs.pop("debounce")

    if db_candidate is not None:
        try:
            db_val = float(db_candidate)
            # If existing is custom and incoming is default (3.5), do NOT overwrite unless explicit
            if existing_is_custom and db_val == 3.5 and not explicit_debounce:
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
        profile_map = {
            "antigravity": "Google Antigravity",
            "cursor": "Cursor AI",
            "windsurf": "Windsurf / Codeium",
            "claude_code": "Claude Code CLI",
            "custom": "Custom AI",
        }
        prof = str(kwargs["ide_profile"]).strip()
        data["monitored_ai_env"] = profile_map.get(prof, prof)

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


def read_project_meta(engine_root: Path | str, project_name: str) -> dict[str, str]:
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
                    found = True

    # Synchronize debounce and debounce_window
    if data.get("debounce_window") and not data.get("debounce"):
        data["debounce"] = data["debounce_window"]
    elif data.get("debounce") and not data.get("debounce_window"):
        data["debounce_window"] = data["debounce"]

    # Synchronize ide_profile and monitored_ai_env
    profile_map = {
        "antigravity": "Google Antigravity",
        "cursor": "Cursor AI",
        "windsurf": "Windsurf / Codeium",
        "claude_code": "Claude Code CLI",
        "custom": "Custom AI",
    }
    if data.get("ide_profile") and not data.get("monitored_ai_env"):
        data["monitored_ai_env"] = profile_map.get(data["ide_profile"], data["ide_profile"])
    elif data.get("monitored_ai_env") and not data.get("ide_profile"):
        rev_map = {
            "google antigravity": "antigravity",
            "cursor ai": "cursor",
            "windsurf / codeium": "windsurf",
            "claude code cli": "claude_code",
            "custom ai": "custom",
        }
        data["ide_profile"] = rev_map.get(data["monitored_ai_env"].lower(), "antigravity")

    # Auto-discover remote origin URL and branch from native .git if not configured
    if data.get("target_path") and (not data.get("remote_url") or data.get("remote_url") in ("Not Linked", "none", "")):
        try:
            try:
                from spd_analysis_engine.scripts.git_shadow import discover_native_git_remote
            except (ImportError, ModuleNotFoundError):
                from scripts.git_shadow import discover_native_git_remote
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
