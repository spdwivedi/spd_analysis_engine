"""
scripts/core/preflight.py
=========================
Startup pre-flight diagnostics and environment verification for the SPD Analysis Engine.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _colored(text: str, color: str) -> str:
    """Format text with ANSI escape codes if interactive terminal."""
    colors = {"green": "\033[92m", "red": "\033[91m", "reset": "\033[0m"}
    if not (hasattr(sys.stdout, "isatty") and (sys.stdout.isatty() or os.name == "nt")):
        return text
    return f"{colors.get(color, '')}{text}{colors['reset']}"


def clean_orphan_lockfiles(engine_root: Path, max_age_seconds: float = 600.0) -> list[str]:
    """Remove orphan lockfiles older than max_age_seconds (default: 10 mins)."""
    cleaned: list[str] = []
    current_dir = engine_root / "current"
    if not current_dir.is_dir():
        return cleaned

    now = time.time()
    for pattern in ["*.lock", "*.pid.stale", "index.lock"]:
        for p in current_dir.rglob(pattern):
            if not p.is_file():
                continue
            try:
                if (now - p.stat().st_mtime) > max_age_seconds:
                    p.unlink(missing_ok=True)
                    cleaned.append(str(p))
                    logger.info("Cleaned orphan lockfile: %s", p)
            except Exception as exc:
                logger.debug("Failed to remove lockfile %s: %s", p, exc)
    return cleaned


def run_preflight_checks(
    engine_root: Path | str | None = None,
    verbose: bool = False,
    print_output: bool = True,
) -> dict[str, Any]:
    """
    Execute all core pre-flight checks before binding the server or starting workers.
    Returns a dictionary of check outcomes.
    """
    if engine_root is None:
        root_path = Path.cwd()
    else:
        root_path = Path(engine_root).resolve()

    results: dict[str, bool] = {
        "python_version": False,
        "dependencies": False,
        "sqlite_wal": False,
        "config_validation": False,
        "storage_tiers": False,
    }
    details: dict[str, Any] = {}

    # 1. Python version >= 3.12
    py_ver = sys.version_info
    results["python_version"] = py_ver >= (3, 12)
    details["python_version"] = f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"

    # 2. Dependencies (watchdog, psutil)
    try:
        import psutil  # noqa: F401
        import watchdog  # noqa: F401
        results["dependencies"] = True
    except ImportError as exc:
        results["dependencies"] = False
        details["dependencies_error"] = str(exc)

    # 3. SQLite WAL Concurrency
    try:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL;")
        # in-memory returns 'memory', test temp file for true WAL
        conn.close()

        test_db = root_path / ".wal_preflight_test.db"
        try:
            fconn = sqlite3.connect(str(test_db))
            fcur = fconn.cursor()
            fcur.execute("PRAGMA journal_mode = WAL;")
            mode = fcur.fetchone()[0]
            fconn.close()
            results["sqlite_wal"] = (str(mode).lower() == "wal")
        finally:
            if test_db.exists():
                try:
                    test_db.unlink(missing_ok=True)
                    Path(str(test_db) + "-wal").unlink(missing_ok=True)
                    Path(str(test_db) + "-shm").unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception as exc:
        results["sqlite_wal"] = False
        details["sqlite_wal_error"] = str(exc)

    # 4. Storage Tiers & Write Access
    tiers = ["current", "last_run", "history", "trash", "ai_data"]
    tiers_ok = True
    tier_statuses = {}
    for tier in tiers:
        t_path = root_path / tier
        try:
            t_path.mkdir(parents=True, exist_ok=True)
            # Test write access
            probe = t_path / f".write_probe_{os.getpid()}.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            tier_statuses[tier] = True
        except Exception as exc:
            tiers_ok = False
            tier_statuses[tier] = False
            details[f"tier_{tier}_error"] = str(exc)
    results["storage_tiers"] = tiers_ok
    details["tiers"] = tier_statuses

    # 5. Clean orphan lockfiles
    cleaned_locks = clean_orphan_lockfiles(root_path, max_age_seconds=600.0)
    details["cleaned_lockfiles"] = cleaned_locks

    # 6. Config Validation (JSON syntax check)
    configs_ok = True
    config_details = {}
    config_files = [
        root_path / "ai_data" / "models_config.json",
        root_path / "ai_data" / "git_config.json",
    ]
    for cfg in config_files:
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    configs_ok = False
                    config_details[cfg.name] = "Not a valid JSON object"
                else:
                    config_details[cfg.name] = "valid"
            except Exception as exc:
                configs_ok = False
                config_details[cfg.name] = f"Syntax error: {exc}"
        else:
            config_details[cfg.name] = "not_found (default fallback)"

    results["config_validation"] = configs_ok
    details["configs"] = config_details

    # Clean summary output table
    if print_output:
        ok_tag = _colored("[ OK ]", "green")
        fail_tag = _colored("[ FAIL ]", "red")

        rows = [
            ("Python 3.12+", results["python_version"]),
            ("SQLite WAL Concurrency", results["sqlite_wal"]),
            ("Config Validation", results["config_validation"]),
            ("Storage Tiers", results["storage_tiers"]),
        ]
        print("")
        for label, passed in rows:
            tag = ok_tag if passed else fail_tag
            dots = "." * max(2, 35 - len(label))
            print(f"[PRE-FLIGHT] {label} {dots} {tag}")
        print("")

    return {
        "all_passed": all(results.values()),
        "results": results,
        "details": details,
    }
