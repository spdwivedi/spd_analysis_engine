"""
scripts/trash/fs_ops.py
=======================
Filesystem operations and directory parsing for trash management.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from .constants import (
        SCOPE_SELECTIVE_BURSTS,
        SCOPE_METADATA_ONLY,
        SCOPE_LOCAL_SESSION,
        SCOPE_SESSION_AND_SHADOW_GIT,
        SCOPE_COMPLETE_ERASE,
        _IST,
        _now_ist_iso,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.trash.constants import (
            SCOPE_SELECTIVE_BURSTS,
            SCOPE_METADATA_ONLY,
            SCOPE_LOCAL_SESSION,
            SCOPE_SESSION_AND_SHADOW_GIT,
            SCOPE_COMPLETE_ERASE,
            _IST,
            _now_ist_iso,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.trash.constants import (
            SCOPE_SELECTIVE_BURSTS,
            SCOPE_METADATA_ONLY,
            SCOPE_LOCAL_SESSION,
            SCOPE_SESSION_AND_SHADOW_GIT,
            SCOPE_COMPLETE_ERASE,
            _IST,
            _now_ist_iso,
        )

logger = logging.getLogger(__name__)


def _strip_readonly_recursive(path: Path | str) -> None:
    """Recursively strip read-only attributes from files and directories."""
    p = Path(path)
    if not p.exists():
        return

    is_windows = sys.platform == "win32"
    kernel32 = None
    if is_windows:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
        except Exception:
            kernel32 = None

    def _strip_single(target: Path | str) -> None:
        target_str = str(target)
        try:
            os.chmod(target_str, stat.S_IWRITE | stat.S_IREAD)
        except Exception:
            pass
        if kernel32:
            try:
                # 128 = FILE_ATTRIBUTE_NORMAL
                kernel32.SetFileAttributesW(target_str, 128)
            except Exception:
                pass

    try:
        if p.is_file() or p.is_symlink():
            _strip_single(p)
        elif p.is_dir():
            _strip_single(p)
            for root, dirs, files in os.walk(p):
                for d in dirs:
                    _strip_single(Path(root) / d)
                for f in files:
                    _strip_single(Path(root) / f)
    except Exception:
        pass


_remove_readonly_recursive = _strip_readonly_recursive


def _safe_rmtree(path: Path | str, retries: int = 6, delay: float = 0.5) -> bool:
    """
    Recursively remove a directory tree, clearing read-only attributes and retrying
    on Windows file locks (WinError 5 / WinError 32).
    """
    p = Path(path)
    if not p.exists():
        return True

    is_windows = sys.platform == "win32"
    kernel32 = None
    if is_windows:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
        except Exception:
            kernel32 = None

    def _clear_and_unlink(func, file_path, exc_info=None):
        try:
            os.chmod(file_path, stat.S_IWRITE | stat.S_IREAD)
        except Exception:
            pass
        if kernel32:
            try:
                kernel32.SetFileAttributesW(str(file_path), 128)
            except Exception:
                pass
        try:
            func(file_path)
        except Exception:
            pass

    def _on_rm_exc(func, file_path, exc):
        _clear_and_unlink(func, file_path)

    for attempt in range(max(1, retries)):
        _strip_readonly_recursive(p)

        if not p.is_dir():
            try:
                p.unlink()
                return True
            except (PermissionError, OSError):
                if attempt < retries - 1:
                    time.sleep(delay)
                    continue
                return False

        try:
            try:
                shutil.rmtree(p, onexc=_on_rm_exc)
            except TypeError:
                shutil.rmtree(p, onerror=_clear_and_unlink)
        except (PermissionError, OSError):
            pass
        except Exception:
            pass

        if not p.exists():
            return True

        # Fallback: manual walk if still lingering
        try:
            for root, dirs, files in os.walk(p, topdown=False):
                for f in files:
                    fp = Path(root) / f
                    _clear_and_unlink(os.unlink, fp)
                for d in dirs:
                    dp = Path(root) / d
                    _clear_and_unlink(os.rmdir, dp)
            _clear_and_unlink(os.rmdir, p)
        except Exception:
            pass

        if not p.exists():
            return True

        if attempt < retries - 1:
            time.sleep(delay)

    return not p.exists()


_force_rmtree = _safe_rmtree


def _safe_move_tree(src: Path | str, dst: Path | str, retries: int = 6, delay: float = 0.5) -> bool:
    """
    Safely move directory tree with Windows lock handling, destination pre-cleaning,
    and copytree fallback.
    """
    s = Path(src)
    d = Path(dst)

    if not s.exists():
        return False

    # Destination Pre-Clean: prevent shutil.move nesting src inside dst (prevents last_run/slug/slug)
    if d.exists():
        _safe_rmtree(d, retries=retries, delay=delay)

    d.parent.mkdir(parents=True, exist_ok=True)
    _strip_readonly_recursive(s)

    for attempt in range(max(1, retries)):
        try:
            if d.exists():
                _safe_rmtree(d, retries=retries, delay=delay)
            shutil.move(str(s), str(d))
            if d.exists() and not s.exists():
                return True
        except (PermissionError, OSError):
            if attempt < retries - 1:
                time.sleep(delay)
                _strip_readonly_recursive(s)
                continue
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
                continue

    # Fallback: copy tree recursively with read-only stripping, then call _safe_rmtree(src)
    try:
        if d.exists():
            _safe_rmtree(d, retries=retries, delay=delay)
        shutil.copytree(str(s), str(d))
        _strip_readonly_recursive(d)
        _safe_rmtree(s, retries=retries, delay=delay)
        return d.exists() and not s.exists()
    except Exception as exc:
        logger.warning("_safe_move_tree fallback failed moving %s to %s: %s", s, d, exc)
        return False


def _parse_orphan_trash_dir(dir_name: str) -> tuple[str, str, str]:
    """
    Synthesize fallback manifest attributes from an orphan trash directory name.
    Expects format: <timestamp>_<project_name>_<scope>
    Returns: (timestamp, project_name, scope)
    """
    m = re.match(r"^(\d{8}_\d{6})_(.+)$", dir_name)
    if m:
        ts_part = m.group(1)
        rest = m.group(2)
        try:
            dt = datetime.strptime(ts_part, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            folder_timestamp = dt.astimezone(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")
        except Exception:
            folder_timestamp = ts_part

        scope_map = {
            "bursts": SCOPE_SELECTIVE_BURSTS,
            "selective_bursts": SCOPE_SELECTIVE_BURSTS,
            "meta": SCOPE_METADATA_ONLY,
            "metadata_only": SCOPE_METADATA_ONLY,
            "session": SCOPE_LOCAL_SESSION,
            "local_session": SCOPE_LOCAL_SESSION,
            "git": SCOPE_SESSION_AND_SHADOW_GIT,
            "session_and_shadow_git": SCOPE_SESSION_AND_SHADOW_GIT,
            "complete": SCOPE_COMPLETE_ERASE,
            "complete_erase": SCOPE_COMPLETE_ERASE,
        }
        for suffix, scope_val in scope_map.items():
            if rest.endswith(f"_{suffix}"):
                proj = rest[: -(len(suffix) + 1)]
                return folder_timestamp, proj or "unknown", scope_val
            elif rest == suffix:
                return folder_timestamp, "unknown", scope_val

        if "_" in rest:
            rparts = rest.rsplit("_", 1)
            return folder_timestamp, rparts[0], rparts[1] or "manual_archive"
        return folder_timestamp, rest, "manual_archive"

    parts = dir_name.split("_")
    if len(parts) >= 3:
        return parts[0], "_".join(parts[1:-1]), parts[-1] or "manual_archive"
    elif len(parts) == 2:
        return _now_ist_iso(), parts[0], parts[1] or "manual_archive"
    return _now_ist_iso(), dir_name, "manual_archive"


__all__ = [
    "_strip_readonly_recursive",
    "_remove_readonly_recursive",
    "_safe_rmtree",
    "_force_rmtree",
    "_safe_move_tree",
    "_parse_orphan_trash_dir",
]
