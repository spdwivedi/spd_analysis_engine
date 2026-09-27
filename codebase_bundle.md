# SPD Analysis Engine — Complete Codebase Source Bundle
Generated on: 2026-09-26 18:25:36 | Target Workspace: D:\Project_Files\SPD_Analysis_Engine\spd_analysis_engine

---

## 1. Codebase Summary & File Index

| Relative File Path | Language | Line Count | Size (KB) | Modularity Status |
| :--- | :--- | :--- | :--- | :--- |
| `start.py` | py | 620 | 20.01 KB | ⚠️ Monolith (>500L) |
| `requirements.txt` | txt | 8 | 0.19 KB | ✓ Modular (<250L) |
| `scripts\__init__.py` | py | 39 | 1.32 KB | ✓ Modular (<250L) |
| `scripts\ai_analyzer.py` | py | 1800 | 78.02 KB | ⚠️ Monolith (>500L) |
| `scripts\diff_calc.py` | py | 340 | 10.64 KB | Medium (250-500L) |
| `scripts\event_bundler.py` | py | 362 | 12.89 KB | Medium (250-500L) |
| `scripts\git_shadow.py` | py | 1085 | 41.59 KB | ⚠️ Monolith (>500L) |
| `scripts\ide_profiles.py` | py | 183 | 6.05 KB | ✓ Modular (<250L) |
| `scripts\orchestrator.py` | py | 1160 | 44.29 KB | ⚠️ Monolith (>500L) |
| `scripts\project_worker.py` | py | 893 | 34.71 KB | ⚠️ Monolith (>500L) |
| `scripts\shell_interceptor.py` | py | 616 | 22.1 KB | ⚠️ Monolith (>500L) |
| `scripts\storage_rotator.py` | py | 1117 | 42.71 KB | ⚠️ Monolith (>500L) |
| `scripts\watcher_fs.py` | py | 203 | 6.11 KB | ✓ Modular (<250L) |
| `scripts\watcher_proc.py` | py | 272 | 9.26 KB | Medium (250-500L) |
| `scripts\web_server.py` | py | 876 | 35.1 KB | ⚠️ Monolith (>500L) |
| `scripts\ai\__init__.py` | py | 33 | 0.79 KB | ✓ Modular (<250L) |
| `scripts\ai\cache_manager.py` | py | 153 | 5.49 KB | ✓ Modular (<250L) |
| `scripts\ai\prompt_builder.py` | py | 297 | 11.16 KB | Medium (250-500L) |
| `scripts\ai\provider_gemini.py` | py | 219 | 8.55 KB | ✓ Modular (<250L) |
| `scripts\ai\provider_ollama.py` | py | 91 | 2.82 KB | ✓ Modular (<250L) |
| `scripts\ai\rate_limiter.py` | py | 109 | 3.78 KB | ✓ Modular (<250L) |
| `scripts\server\__init__.py` | py | 15 | 0.35 KB | ✓ Modular (<250L) |
| `scripts\server\routes_ai.py` | py | 393 | 16.88 KB | Medium (250-500L) |
| `scripts\server\routes_projects.py` | py | 932 | 41.56 KB | ⚠️ Monolith (>500L) |
| `web\app.js` | js | 3376 | 137.84 KB | ⚠️ Monolith (>500L) |
| `web\index.html` | html | 1272 | 66.18 KB | ⚠️ Monolith (>500L) |
| `web\style.css` | css | 3387 | 66.72 KB | ⚠️ Monolith (>500L) |

**Total Files:** 27 | **Total Code Lines:** 19851 | **Total Uncompressed Size:** 0.71 MB

---

## 2. Complete Source Code

### [1/27] File: `start.py`
- **Lines:** 620 | **Size:** 20.01 KB | **Type:** py

```python
"""
start.py
========
Unified CLI entry point for the SPD Analysis Engine (Phase 1).

Usage
-----
    python start.py start  --project <name> --path <target_path> [--scaffold]
    python start.py stop   --project <name>
    python start.py status [--project <name>]
    python start.py list

Description
-----------
Each sub-command maps to a method on ``Supervisor``.  The Supervisor instance
is lazily created on first use and its state is lost when the process exits
(i.e. this CLI is **not** a persistent daemon — it is a thin control layer
over the subprocess-based workers).

For long-running orchestration, embed ``Supervisor`` in a daemon process or
call it from a future WebSocket server.

Output formatting
-----------------
* When stdout is a TTY, ANSI colour codes are used for status tags.
* When piped or redirected, plain ASCII is used automatically.
* ``--json`` flag switches all output to newline-delimited JSON for
  programmatic consumers.

Exit codes
----------
0  – Success
1  – User / argument error
2  – Runtime / engine error
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure spd_analysis_engine package is importable when start.py is invoked
# from the repo root.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent  # …/spd_analysis_engine/
_PKG_PARENT = _SCRIPT_DIR.parent               # …/Project_Files/SPD_Analysis_Engine/
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

from spd_analysis_engine.scripts.orchestrator import Supervisor
from spd_analysis_engine.scripts.storage_rotator import StorageRotator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,   # keep quiet by default; -v raises to INFO
    format="%(levelname)-8s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy global Supervisor (one per CLI invocation; not persistent across calls)
# ---------------------------------------------------------------------------
_supervisor: Supervisor | None = None


def _get_supervisor(engine_root: Path) -> Supervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = Supervisor(engine_root)
    return _supervisor


# ---------------------------------------------------------------------------
# Windows: enable ANSI virtual terminal processing + UTF-8 stdout
# ---------------------------------------------------------------------------
if os.name == "nt":
    import io
    # Reconfigure stdout to UTF-8 so box/tag characters don't hit cp1252
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # Enable ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x0004) on stdout
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:  # noqa: BLE001
        pass  # older Windows or redirected; colours will be stripped below

# ---------------------------------------------------------------------------
# Colour / formatting helpers
# ---------------------------------------------------------------------------

_COLOUR_SUPPORT = sys.stdout.isatty()

# ANSI codes
_C = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "green":  "\033[32m",
    "yellow": "\033[33m",
    "red":    "\033[31m",
    "cyan":   "\033[36m",
    "grey":   "\033[90m",
}


def _col(text: str, *codes: str) -> str:
    """Wrap *text* in ANSI codes if colour output is enabled."""
    if not _COLOUR_SUPPORT:
        return text
    prefix = "".join(_C.get(c, "") for c in codes)
    return f"{prefix}{text}{_C['reset']}"


def _tag(label: str, color: str) -> str:
    """Render a ``[ LABEL ]`` status tag."""
    padded = f" {label:<8}"
    return _col(f"[{padded}]", color, "bold")


def _hr(width: int = 72, char: str = "-") -> str:
    return _col(char * width, "grey")


def _header(title: str) -> str:
    bar = _hr()
    return f"\n{bar}\n  {_col(title, 'bold', 'cyan')}\n{bar}"


def _fmt_bytes(mb: float | None) -> str:
    if mb is None:
        return "N/A"
    return f"{mb:.1f} MB"


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


# ---------------------------------------------------------------------------
# Table renderer
# ---------------------------------------------------------------------------

def _print_table(
    rows: list[dict[str, Any]],
    columns: list[tuple[str, str, int]],
    title: str = "",
) -> None:
    """
    Print a simple ASCII/ANSI table.

    Parameters
    ----------
    rows    : List of row dicts.
    columns : List of ``(header, dict_key, min_width)`` tuples.
    title   : Optional table title printed above the header row.
    """
    if title:
        print(_header(title))

    if not rows:
        print(_col("  (no entries)", "grey"))
        print()
        return

    # Compute column widths
    widths = [max(col[2], len(col[0])) for col in columns]
    for row in rows:
        for i, (_, key, _) in enumerate(columns):
            val = str(row.get(key, ""))
            widths[i] = max(widths[i], len(val))

    sep = "  "
    header = sep.join(
        _col(columns[i][0].ljust(widths[i]), "bold") for i in range(len(columns))
    )
    divider = sep.join("-" * widths[i] for i in range(len(columns)))

    print(f"\n  {header}")
    print(f"  {divider}")

    for row in rows:
        cells = []
        for i, (_, key, w) in enumerate(columns):
            val = str(row.get(key, ""))
            cells.append(val.ljust(w))
        print(f"  {sep.join(cells)}")

    print()


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------

def cmd_start(args: argparse.Namespace, sup: Supervisor) -> int:
    """Handle ``start`` sub-command."""
    print(
        f"\n{_tag('START', 'green')} "
        f"project={_col(args.project, 'bold')}  "
        f"path={_col(args.path, 'cyan')}"
        f"{'  --scaffold' if args.scaffold else ''}"
    )

    try:
        result = sup.start_project(
            name=args.project,
            path=args.path,
            scaffold=args.scaffold,
            debounce=getattr(args, "debounce", 3.5),
            track_reads=getattr(args, "track_reads", True),
            track_exec=getattr(args, "track_exec", True),
            shadow_git=getattr(args, "shadow_git", True),
            git_init_primary=getattr(args, "git_init_primary", False),
            ide_profile=getattr(args, "ide_profile", "antigravity"),
            ide_custom_marker=getattr(args, "ide_custom_marker", None),
        )
    except ValueError as exc:
        print(f"\n{_tag('ERROR', 'red')} {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\n{_tag('ERROR', 'red')} Unexpected error: {exc}", file=sys.stderr)
        logger.exception("start_project failed")
        return 2

    if args.json:
        print(json.dumps(result))
        return 0

    log_path = result.get("log_path", "N/A")
    print(
        f"\n  {_col('Worker started successfully', 'green')}\n"
        f"  PID          : {_col(str(result['pid']), 'bold')}\n"
        f"  Project      : {result['project']}\n"
        f"  Target path  : {result['target_path']}\n"
        f"  Log file     : {_col(log_path, 'grey')}\n"
    )

    view_cmd = f"type \"{log_path}\"" if os.name == "nt" else f"tail -f \"{log_path}\""
    print(
        _col(
            f"  Worker is running detached in the background.\n"
            f"  View logs    : {view_cmd}\n"
            f"  Check status : python start.py status\n"
            f"  Stop worker  : python start.py stop --project {args.project}\n",
            "grey",
        )
    )

    # Wait for the first heartbeat / status file, then display status
    print(f"  {_col('Waiting for first heartbeat…', 'grey')}")
    deadline = time.monotonic() + 5.0
    status: list = []
    while time.monotonic() < deadline:
        time.sleep(0.4)
        status = sup.get_status(args.project)
        if status and status[0].get("session_id") is not None:
            break

    if status:
        _print_status_table(status)
    else:
        print(f"\n  {_col('(status file not yet available — run status in a moment)', 'grey')}\n")

    return 0


def cmd_stop(args: argparse.Namespace, sup: Supervisor) -> int:
    """Handle ``stop`` sub-command."""
    print(
        f"\n{_tag('STOP', 'yellow')} "
        f"project={_col(args.project, 'bold')}"
    )

    try:
        result = sup.stop_project(args.project)
    except KeyError as exc:
        print(
            f"\n{_tag('ERROR', 'red')} {exc}\n"
            "  Hint: use 'python start.py list' to see running projects.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\n{_tag('ERROR', 'red')} {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result))
        return 0

    exit_tag = _tag("OK", "green") if result["exit_code"] == 0 else _tag("EXIT", "yellow")
    print(
        f"\n  {exit_tag}  "
        f"Project '{_col(result['project'], 'bold')}' stopped.  "
        f"Exit code: {result['exit_code']}\n"
    )
    return 0


def _print_status_table(statuses: list[dict[str, Any]]) -> None:
    """Render the status table for a list of worker dicts."""
    rows = []
    for s in statuses:
        if s["alive"]:
            alive_tag = _tag("LIVE", "green")
        else:
            alive_tag = _tag("DEAD", "red")

        # last_heartbeat_s is None until the first status file is written (~2 s)
        hb_age = s.get("last_heartbeat_s")
        hb_str = (f"{hb_age:.1f}s ago" if hb_age is not None else "pending") if s["alive"] else "-"

        rows.append({
            "project":   s["project"],
            "status":    alive_tag,
            "pid":       str(s["pid"]) if s["pid"] else "-",
            "uptime":    _fmt_uptime(s["uptime_s"]) if s["alive"] else "-",
            "memory":    _fmt_bytes(s.get("memory_mb")),
            "cpu":       f"{s['cpu_percent']:.1f}%" if s.get("cpu_percent") is not None else "N/A",
            "heartbeat": hb_str,
            "session":   str(s["session_id"]) if s["session_id"] is not None else "-",
        })

    _print_table(
        rows,
        columns=[
            ("PROJECT",   "project",   16),
            ("STATUS",    "status",    12),
            ("PID",       "pid",        8),
            ("UPTIME",    "uptime",    10),
            ("MEMORY",    "memory",    10),
            ("CPU",       "cpu",        7),
            ("HEARTBEAT", "heartbeat", 12),
            ("SESSION",   "session",    8),
        ],
        title="Worker Status",
    )


def cmd_status(args: argparse.Namespace, sup: Supervisor) -> int:
    """Handle ``status`` sub-command."""
    project = getattr(args, "project", None)
    statuses = sup.get_status(project)

    if args.json:
        print(json.dumps(statuses, indent=2))
        return 0

    if not statuses:
        msg = (
            f"No running worker for project '{project}'."
            if project
            else "No workers are currently running."
        )
        print(f"\n{_tag('INFO', 'cyan')} {msg}\n")
        return 0

    _print_status_table(statuses)
    return 0


def cmd_list(args: argparse.Namespace, sup: Supervisor) -> int:
    """Handle ``list`` sub-command."""
    data = sup.list_projects()

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    running = data.get("running", {})
    on_disk = data.get("on_disk", {})

    print(_header("Project Registry"))

    # --- Running workers table ---
    run_rows = [
        {"project": name, "pid": str(pid)}
        for name, pid in running.items()
    ]
    _print_table(
        run_rows,
        columns=[("PROJECT (running)", "project", 24), ("PID", "pid", 8)],
        title="",
    )

    # --- On-disk history table ---
    disk_rows = []
    for slug, info in on_disk.items():
        tiers = []
        if info["current"]:
            tiers.append(_col("current", "green"))
        if info["last_run"]:
            tiers.append(_col("last_run", "yellow"))
        if info["history_runs"]:
            tiers.append(
                _col(f"history×{len(info['history_runs'])}", "grey")
            )
        disk_rows.append(
            {
                "slug":    slug,
                "tiers":  "  ".join(tiers) if tiers else "—",
                "runs":   str(len(info["history_runs"])),
            }
        )

    print(_col("  On-disk snapshots:", "bold"))
    _print_table(
        disk_rows,
        columns=[
            ("SLUG",         "slug",  24),
            ("TIERS",        "tiers", 32),
            ("HISTORY RUNS", "runs",   4),
        ],
        title="",
    )
    return 0


def cmd_ui(args: argparse.Namespace, sup: Supervisor) -> int:
    """Handle ``ui`` sub-command: launches the Web Control Portal."""
    from spd_analysis_engine.scripts.web_server import EngineWebServer
    import webbrowser

    port = args.port
    engine_root = sup._root
    server = EngineWebServer(engine_root=engine_root, port=port)

    actual_port = server.start(background=True)
    url = f"http://localhost:{actual_port}"

    print(
        f"\n{_tag('WEB UI', 'green')} "
        f"Server active at: {_col(url, 'bold', 'cyan')}"
    )
    print(f"  Serving dashboard from: {_col(str(server.web_dir), 'grey')}")
    print(f"  Press Ctrl+C to terminate web portal.\n")

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception as exc:
            logger.debug("Could not open browser automatically: %s", exc)

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print(f"\n{_tag('SHUTDOWN', 'yellow')} Stopping Web Control Portal...")
        server.shutdown()
        print("  [ OK ] Web server stopped.\n")

    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all sub-commands."""
    root = argparse.ArgumentParser(
        prog="start",
        description=textwrap.dedent("""\
            SPD Analysis Engine – Phase 1 CLI
            =================================
            Monitor project directories with isolated worker processes.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    root.add_argument(
        "--engine-root",
        default=str(_SCRIPT_DIR),
        metavar="DIR",
        help=f"Path to the spd_analysis_engine root (default: {_SCRIPT_DIR})",
    )
    root.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit output as JSON instead of formatted tables",
    )
    root.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (INFO) logging",
    )

    subs = root.add_subparsers(dest="command", metavar="COMMAND")
    subs.required = True

    # ---- start ----
    p_start = subs.add_parser(
        "start",
        help="Start monitoring a project directory",
        description="Spawn an isolated worker process for the given project.",
    )
    p_start.add_argument("--project", "-p", required=True, metavar="NAME",
                         help="Unique project name")
    p_start.add_argument("--path", required=True, metavar="TARGET_PATH",
                         help="Absolute path to the project directory")
    p_start.add_argument("--scaffold", action="store_true", default=False,
                         help="Create the target directory if it does not exist")
    p_start.add_argument("--debounce", type=float, default=3.5, metavar="SECONDS",
                         help="Sliding debounce quiet window in seconds (default: 3.5)")
    p_start.add_argument("--track-reads", action=argparse.BooleanOptionalAction, default=True,
                         help="Track file read/analysis handles via psutil (default: True)")
    p_start.add_argument("--track-exec", action=argparse.BooleanOptionalAction, default=True,
                         help="Track terminal execution commands (default: True)")
    p_start.add_argument("--shadow-git", action=argparse.BooleanOptionalAction, default=True,
                         help="Maintain shadow git micro-versioning (default: True)")
    p_start.add_argument("--git-init-primary", action="store_true", default=False,
                         help="Initialize primary git repo if missing (default: False)")
    p_start.add_argument("--ide-profile", default="antigravity",
                         choices=["antigravity", "cursor", "windsurf", "claude_code", "custom"],
                         help="Monitored AI environment profile (default: antigravity)")
    p_start.add_argument("--ide-custom-marker", default=None,
                         help="Custom process marker or executable name when using custom profile")

    # ---- stop ----
    p_stop = subs.add_parser(
        "stop",
        help="Stop a running project worker",
        description="Send a termination signal and archive the session.",
    )
    p_stop.add_argument("--project", "-p", required=True, metavar="NAME",
                        help="Name of the project to stop")

    # ---- status ----
    p_status = subs.add_parser(
        "status",
        help="Show status of running workers",
        description="Display PID, uptime, memory, and heartbeat for workers.",
    )
    p_status.add_argument("--project", "-p", default=None, metavar="NAME",
                          help="Limit output to a single project")

    # ---- list ----
    subs.add_parser(
        "list",
        help="List all projects across current/last_run/history tiers",
        description="Show every project slug found on disk and their tier locations.",
    )

    # ---- ui ----
    p_ui = subs.add_parser(
        "ui",
        help="Launch the Web Control Portal in browser",
        description="Start the local HTTP web server and open the web dashboard.",
    )
    p_ui.add_argument("--port", type=int, default=8765, metavar="PORT",
                      help="Port to bind web server to (default: 8765)")
    p_ui.add_argument("--no-browser", action="store_true", default=False,
                      help="Do not open default web browser automatically")

    return root


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """
    Parse arguments, create the Supervisor, dispatch to the correct handler.

    Returns the process exit code (0 = success, 1 = user error, 2 = engine error).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Propagate --json and --verbose to sub-commands that may not define them
    if not hasattr(args, "json"):
        args.json = False

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("spd_analysis_engine").setLevel(logging.INFO)

    engine_root = Path(args.engine_root).resolve()
    sup = _get_supervisor(engine_root)

    dispatch = {
        "start":  cmd_start,
        "stop":   cmd_stop,
        "status": cmd_status,
        "list":   cmd_list,
        "ui":     cmd_ui,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args, sup)


if __name__ == "__main__":
    sys.exit(main())
```

---

### [2/27] File: `requirements.txt`
- **Lines:** 8 | **Size:** 0.19 KB | **Type:** txt

```text
# SPD Analysis Engine - Phase 1 Dependencies
# Minimum Python version: 3.10+

# File system event monitoring
watchdog>=4.0.0

# Process introspection (CPU, memory, PID tracking)
psutil>=5.9.0
```

---

### [3/27] File: `scripts\__init__.py`
- **Lines:** 39 | **Size:** 1.32 KB | **Type:** py

```python
"""
SPD Analysis Engine – scripts package
======================================
Exposes the public API surface used by start.py, tests, and future
WebSocket / REST endpoints so that every feature can be driven either
from the CLI *or* from Python code.

Exported symbols
----------------
SessionDB       – SQLite WAL-backed database manager (storage_rotator)
StorageRotator  – Lifecycle directory rotator (storage_rotator)
Supervisor      – Multi-worker process supervisor (orchestrator)
"""

from .storage_rotator import SessionDB, StorageRotator  # noqa: F401
from .orchestrator import Supervisor  # noqa: F401
from .diff_calc import DiffCalculator  # noqa: F401
from .event_bundler import EventBundler  # noqa: F401
from .watcher_fs import ProjectWatcher  # noqa: F401
from .web_server import EngineWebServer  # noqa: F401
from .git_shadow import ShadowGit, GitCredentialManager  # noqa: F401
from .watcher_proc import ProcessInspector  # noqa: F401
from .shell_interceptor import ExecutionTracker  # noqa: F401
from .ai_analyzer import AISynthesizer  # noqa: F401

__all__ = [
    "SessionDB",
    "StorageRotator",
    "Supervisor",
    "DiffCalculator",
    "EventBundler",
    "ProjectWatcher",
    "EngineWebServer",
    "ShadowGit",
    "GitCredentialManager",
    "ProcessInspector",
    "ExecutionTracker",
    "AISynthesizer",
]
```

---

### [4/27] File: `scripts\ai_analyzer.py`
- **Lines:** 1800 | **Size:** 78.02 KB | **Type:** py

```python
"""
scripts/ai_analyzer.py
======================
On-Demand AI Intelligence Layer for the SPD Analysis Engine (Phase 5).

Analyzes unified diffs and file patches to generate plain-English intent,
granular modification summaries, and functional capability gains.

Features
--------
1. On-Demand Only: Consumes zero tokens/system resources until explicitly invoked
   via Web Portal UI ("Analyze with AI" button) or REST API.
2. Dual Provider Support:
   - Ollama: Local, offline inference (e.g. qwen2.5-coder:7b) via http://localhost:11434.
   - Gemini: Cloud inference via official Google GenAI SDK (gemini-3.8-flash / gemini-2.0-flash).
3. SHA-256 Diff Caching: Caches analyses in ``ai_data/cache/<sha256>.json`` to prevent
   redundant model invocations for identical code changes.
4. Smart Diff Chunking: Truncates oversized diffs cleanly while preserving structural context.
5. Offline Resilient: Graceful fallback if neither AI provider is reachable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    from .ai import (
        RateLimitManager,
        AICacheManager,
        is_sanitized_patch,
        detect_tech_stack,
        generate_directory_tree,
        chunk_diff,
        build_burst_prompt,
        build_command_prompt,
        call_gemini,
        test_gemini_connection,
        call_ollama,
        test_ollama_connection,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ai import (
            RateLimitManager,
            AICacheManager,
            is_sanitized_patch,
            detect_tech_stack,
            generate_directory_tree,
            chunk_diff,
            build_burst_prompt,
            build_command_prompt,
            call_gemini,
            test_gemini_connection,
            call_ollama,
            test_ollama_connection,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.ai import (
            RateLimitManager,
            AICacheManager,
            is_sanitized_patch,
            detect_tech_stack,
            generate_directory_tree,
            chunk_diff,
            build_burst_prompt,
            build_command_prompt,
            call_gemini,
            test_gemini_connection,
            call_ollama,
            test_ollama_connection,
        )


_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    """Return ISO-8601 formatted timestamp in IST (UTC+5:30)."""
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


def _now_ist_header() -> str:
    """Return standard release header timestamp in IST (e.g. 2026-09-24 23:14 IST)."""
    return datetime.now(_IST).strftime("%Y-%m-%d %H:%M IST")


SANITIZATION_IGNORE_PATTERNS: tuple[str, ...] = (
    ".sf/", ".sf\\", "/.sf/", "\\.sf\\",
    ".sfdx/", ".sfdx\\", "/.sfdx/", "\\.sfdx\\",
    "catalog.json",
    ".__staging__",
    ".tmp",
    "node_modules/", "node_modules\\",
    "__pycache__/", "__pycache__\\",
    ".git/", ".git\\",
)


def is_sanitized_patch(file_path: str) -> bool:
    """Return True if patch file is clean project source code, False if noise/temporary artifact."""
    if not file_path:
        return False
    fp = file_path.lower().replace("\\", "/")
    if fp.startswith(".sf/") or fp.startswith(".sfdx/") or fp.startswith("node_modules/") or fp.startswith("__pycache__/") or fp.startswith(".git/"):
        return False
    if any(seg in fp for seg in ("/.sf/", "/.sfdx/", "/node_modules/", "/__pycache__/", "/.git/")):
        return False
    if "catalog.json" in fp or ".__staging__" in fp or fp.endswith(".tmp"):
        return False
    return True


class AISynthesizer:
    """
    On-Demand AI Intelligence synthesizer for code diffs and burst events.

    Parameters
    ----------
    engine_root : Path | str | None
        Root directory of ``spd_analysis_engine``. If None, auto-detected from file location.
    """

    @staticmethod
    def detect_tech_stack(workspace_path: Path | str | None) -> str:
        """Detect workspace tech stack from project indicator files."""
        if not workspace_path:
            return "Generic Software Codebase"
        wp = Path(workspace_path)
        if not wp.exists() or not wp.is_dir():
            return "Generic Software Codebase"

        indicators: list[str] = []

        # JavaScript / Node / Web
        pkg_json = wp / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "react" in deps:
                    indicators.append("React")
                elif "vue" in deps:
                    indicators.append("Vue")
                elif "svelte" in deps:
                    indicators.append("Svelte")
                elif "next" in deps:
                    indicators.append("Next.js")
                if "vite" in deps:
                    indicators.append("Vite")
                if "typescript" in deps:
                    indicators.append("TypeScript")
                elif not indicators:
                    indicators.append("Node.js")
            except Exception:
                indicators.append("Node.js")
        elif any(wp.glob("*.html")) or any((wp / "web").glob("*.html") if (wp / "web").exists() else ()):
            indicators.append("Vanilla HTML5/CSS3/JavaScript")

        # Python
        py_indicators = [wp / "requirements.txt", wp / "pyproject.toml", wp / "setup.py"]
        if any(p.exists() for p in py_indicators) or any(wp.glob("*.py")) or (wp / "scripts").exists():
            indicators.append("Python")

        # Rust, Go, Java, Salesforce
        if (wp / "Cargo.toml").exists():
            indicators.append("Rust")
        if (wp / "go.mod").exists():
            indicators.append("Go")
        if (wp / "pom.xml").exists() or (wp / "build.gradle").exists():
            indicators.append("Java")
        if (wp / "sfdx-project.json").exists() or (wp / ".sf").exists():
            indicators.append("Salesforce DX / Apex")

        if not indicators:
            return "Polyglot Software Project"
        return " + ".join(indicators)

    @staticmethod
    def generate_directory_tree(workspace_path: Path | str | None, max_depth: int = 2) -> str:
        """Generate lightweight 2-level directory tree of the workspace, excluding ignore patterns."""
        if not workspace_path:
            return "(Workspace directory tree not available)"
        wp = Path(workspace_path)
        if not wp.exists() or not wp.is_dir():
            return f"{wp.name}/ (Path not found)"

        ignore_set = {
            ".git", ".sf", ".sfdx", "node_modules", "__pycache__", ".venv", "venv",
            ".idea", ".vscode", "trash", "ai_data", ".pytest_cache", ".mypy_cache",
            "current", "last_run", "history", "dist", "build"
        }

        tree_lines = [f"{wp.name}/"]

        def _walk(curr: Path, depth: int, prefix: str) -> None:
            if depth > max_depth:
                return
            try:
                items = sorted(curr.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            except Exception:
                return

            filtered = [
                it for it in items
                if it.name not in ignore_set
                and not it.name.startswith(".__staging__")
                and not it.name.endswith(".tmp")
                and not (it.name.startswith(".") and it.is_dir())
            ]

            for i, item in enumerate(filtered[:18]):
                is_last = (i == len(filtered) - 1 or i == 17)
                connector = "└── " if is_last else "├── "
                suffix = "/" if item.is_dir() else ""
                tree_lines.append(f"{prefix}{connector}{item.name}{suffix}")
                if item.is_dir() and depth < max_depth:
                    sub_prefix = prefix + ("    " if is_last else "│   ")
                    _walk(item, depth + 1, sub_prefix)
            if len(filtered) > 18:
                tree_lines.append(f"{prefix}└── ... [{len(filtered) - 18} more items]")

        _walk(wp, 1, "")
        return "\n".join(tree_lines[:40])

    rate_limiter: RateLimitManager = RateLimitManager(max_rpm=15)

    def __init__(self, engine_root: Path | str | None = None) -> None:
        if engine_root is None:
            self.engine_root = Path(__file__).resolve().parent.parent
        else:
            self.engine_root = Path(engine_root).resolve()

        self.ai_data_dir = self.engine_root / "ai_data"
        self.config_path = self.ai_data_dir / "models_config.json"
        self.cache_dir = self.ai_data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_mgr = AICacheManager(self.cache_dir)

        self._config: dict[str, Any] = self.load_config(self.engine_root)

    @staticmethod
    def get_config_path(engine_root: Path | str | None = None) -> Path:
        if engine_root is None:
            engine_root = Path(__file__).resolve().parent.parent
        return Path(engine_root) / "ai_data" / "models_config.json"

    @staticmethod
    def _load_env_file(engine_root: Path | str | None = None) -> dict[str, str]:
        """Load key-value pairs from ai_data/.env, project .env, or parent .env without external libraries."""
        if engine_root is None:
            engine_root = Path(__file__).resolve().parent.parent
        root = Path(engine_root)
        candidates = [
            root / "ai_data" / ".env",
            root / ".env",
            root.parent / ".env",
        ]
        env_vars: dict[str, str] = {}
        for cand in candidates:
            if cand.exists() and cand.is_file():
                try:
                    for line in cand.read_text(encoding="utf-8", errors="replace").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k and k not in env_vars:
                                env_vars[k] = v
                except Exception as exc:
                    logger.debug("Failed to read %s: %s", cand, exc)
        return env_vars

    @classmethod
    def load_config(cls, engine_root: Path | str | None = None) -> dict[str, Any]:
        """Load or reload model configuration adhering to strict credential precedence."""
        config_path = cls.get_config_path(engine_root)
        default_cfg: dict[str, Any] = {
            "default_provider": "gemini" if os.environ.get("GEMINI_API_KEY") else "heuristic",
            "preferred_provider": "gemini" if os.environ.get("GEMINI_API_KEY") else "heuristic",
            "ollama": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434",
                "default_model": "qwen2.5-coder:7b",
                "timeout_seconds": 60,
                "options": {"temperature": 0.2, "top_p": 0.9, "num_ctx": 4096},
            },
            "gemini": {
                "enabled": True,
                "api_key": "",
                "default_model": "gemini-3.1-flash-lite",
                "timeout_seconds": 30,
                "options": {"temperature": 0.2, "max_output_tokens": 8192},
            },
            "analysis": {
                "max_diff_lines_for_summary": 250,
            },
        }

        raw_config_key = ""
        saved_keys: list[dict[str, Any]] = []
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if isinstance(v, dict) and isinstance(default_cfg.get(k), dict):
                        default_cfg[k].update(v)
                    else:
                        default_cfg[k] = v

                gem_sub = data.get("gemini", {})
                if isinstance(gem_sub, dict):
                    raw_config_key = str(gem_sub.get("api_key", "")).strip()
                if not raw_config_key and "gemini_api_key" in data:
                    raw_config_key = str(data["gemini_api_key"]).strip()

                saved_raw = data.get("saved_gemini_keys") or data.get("saved_keys") or []
                if isinstance(saved_raw, list):
                    saved_keys = [dict(x) for x in saved_raw if isinstance(x, dict)]
            except Exception as exc:
                logger.warning("Failed to parse models_config.json: %s; using defaults.", exc)

        # Strict Credential Precedence:
        # 1. models_config.json (explicitly saved by user via UI or file)
        # 2. .env file (ai_data/.env or root .env)
        # 3. os.environ["GEMINI_API_KEY"]
        resolved_key = ""
        key_source = "none"

        if raw_config_key and not raw_config_key.startswith("__REPLACE_"):
            resolved_key = raw_config_key
            key_source = "models_config.json"
        else:
            env_file_vars = cls._load_env_file(engine_root)
            env_file_key = env_file_vars.get("GEMINI_API_KEY", "").strip()
            if env_file_key and not env_file_key.startswith("__REPLACE_"):
                resolved_key = env_file_key
                key_source = ".env"
            else:
                os_key = os.environ.get("GEMINI_API_KEY", "").strip()
                if os_key and not os_key.startswith("__REPLACE_"):
                    resolved_key = os_key
                    key_source = "os.environ"

        # Mirror convenience keys
        pref = default_cfg.get("preferred_provider") or default_cfg.get("default_provider", "heuristic")
        default_cfg["default_provider"] = pref
        default_cfg["preferred_provider"] = pref

        ollama_cfg = default_cfg.setdefault("ollama", {})
        default_cfg["ollama_base_url"] = ollama_cfg.get("base_url", "http://127.0.0.1:11434")
        default_cfg["ollama_model"] = ollama_cfg.get("default_model", "qwen2.5-coder:7b")

        gem_cfg = default_cfg.setdefault("gemini", {})
        gem_cfg["api_key"] = resolved_key
        if gem_cfg.get("default_model") == "gemini-2.5-flash":
            gem_cfg["default_model"] = "gemini-3.1-flash-lite"

        default_cfg["gemini_api_key"] = resolved_key
        default_cfg["gemini_model"] = gem_cfg.get("default_model", "gemini-3.1-flash-lite")
        default_cfg["active_key_source"] = key_source
        default_cfg["active_key_suffix"] = f"...{resolved_key[-4:]}" if len(resolved_key) >= 4 else ("(not set)" if not resolved_key else resolved_key)

        # Ensure raw_config_key from models_config.json is registered in saved_gemini_keys vault
        if raw_config_key and not raw_config_key.startswith("__REPLACE_"):
            existing = next((k for k in saved_keys if k.get("key") == raw_config_key), None)
            if existing:
                if not existing.get("id"):
                    existing["id"] = f"key_{uuid.uuid4().hex[:8]}"
            else:
                sfx = f"...{raw_config_key[-4:]}" if len(raw_config_key) >= 4 else raw_config_key
                saved_keys.insert(0, {
                    "id": f"key_{uuid.uuid4().hex[:8]}",
                    "label": f"Account ({sfx})",
                    "key": raw_config_key,
                    "added_at": _now_ist_iso(),
                    "last_used": _now_ist_iso(),
                })

        default_cfg["saved_gemini_keys"] = saved_keys
        default_cfg["saved_keys"] = saved_keys

        return default_cfg

    @classmethod
    def mask_config(cls, cfg: dict[str, Any]) -> dict[str, Any]:
        """Return config with any sensitive Gemini API keys masked and suffix exposed."""
        copied = json.loads(json.dumps(cfg))
        gem_cfg = copied.get("gemini", {})
        api_key = gem_cfg.get("api_key", "")
        if api_key and "__REPLACE_" not in api_key:
            if len(api_key) <= 8:
                gem_cfg["api_key"] = "****"
            else:
                gem_cfg["api_key"] = f"{api_key[:4]}****{api_key[-4:]}"

        top_key = copied.get("gemini_api_key", "")
        if top_key and "__REPLACE_" not in top_key:
            if len(top_key) <= 8:
                copied["gemini_api_key"] = "****"
            else:
                copied["gemini_api_key"] = f"{top_key[:4]}****{top_key[-4:]}"

        copied["active_key_suffix"] = cfg.get("active_key_suffix", "(not set)")
        copied["active_key_source"] = cfg.get("active_key_source", "none")

        # Mask keys in vault
        masked_vault = []
        act_key = cfg.get("gemini_api_key", "")
        for item in cfg.get("saved_gemini_keys", []):
            raw_k = str(item.get("key", "")).strip()
            if not raw_k or raw_k.startswith("__REPLACE_"):
                continue
            sfx = f"...{raw_k[-4:]}" if len(raw_k) >= 4 else raw_k
            masked = f"{raw_k[:4]}****{raw_k[-4:]}" if len(raw_k) > 8 else "****"
            is_active = bool(raw_k and (raw_k == act_key or raw_k == cfg.get("gemini", {}).get("api_key", "")))
            masked_vault.append({
                "id": item.get("id") or f"key_{uuid.uuid4().hex[:8]}",
                "label": item.get("label") or f"Account ({sfx})",
                "suffix": sfx,
                "masked_key": masked,
                "added_at": item.get("added_at") or _now_ist_iso(),
                "last_used": item.get("last_used") or _now_ist_iso(),
                "active": is_active,
            })
        copied["saved_gemini_keys"] = masked_vault
        copied["saved_keys"] = masked_vault

        return copied

    @classmethod
    def save_config(cls, config_dict: dict[str, Any], engine_root: Path | str | None = None) -> dict[str, Any]:
        """Save updated configuration to models_config.json."""
        current = cls.load_config(engine_root)
        config_path = cls.get_config_path(engine_root)

        # Update preferred_provider
        pref = config_dict.get("preferred_provider") or config_dict.get("default_provider")
        if pref:
            clean_pref = str(pref).strip().lower()
            current["default_provider"] = clean_pref
            current["preferred_provider"] = clean_pref

        # Update ollama
        ollama_cur = current.setdefault("ollama", {})
        if "ollama_base_url" in config_dict:
            ollama_cur["base_url"] = str(config_dict["ollama_base_url"]).strip().rstrip("/")
            current["ollama_base_url"] = ollama_cur["base_url"]
        if "ollama_model" in config_dict:
            ollama_cur["default_model"] = str(config_dict["ollama_model"]).strip()
            current["ollama_model"] = ollama_cur["default_model"]

        if "ollama" in config_dict and isinstance(config_dict["ollama"], dict):
            for k, v in config_dict["ollama"].items():
                if k == "base_url":
                    ollama_cur["base_url"] = str(v).strip().rstrip("/")
                    current["ollama_base_url"] = ollama_cur["base_url"]
                elif k == "default_model":
                    ollama_cur["default_model"] = str(v).strip()
                    current["ollama_model"] = ollama_cur["default_model"]
                elif k in ("enabled", "timeout_seconds"):
                    ollama_cur[k] = v

        # Key Vault operations
        gem_cur = current.setdefault("gemini", {})
        saved_keys = list(current.get("saved_gemini_keys", []))

        # 1. Delete key if requested
        if "delete_key_id" in config_dict and config_dict["delete_key_id"]:
            del_id = str(config_dict["delete_key_id"]).strip()
            del_item = None
            for idx, k in enumerate(saved_keys):
                if k.get("id") == del_id:
                    del_item = saved_keys.pop(idx)
                    break
            if del_item and del_item.get("key") == current.get("gemini_api_key"):
                if saved_keys:
                    nxt = saved_keys[0].get("key", "")
                    current["gemini_api_key"] = nxt
                    gem_cur["api_key"] = nxt
                    current["active_key_suffix"] = f"...{nxt[-4:]}" if len(nxt) >= 4 else nxt
                else:
                    current["gemini_api_key"] = ""
                    gem_cur["api_key"] = ""
                    current["active_key_suffix"] = "(not set)"

        # 2. Select existing key from vault if requested
        if "select_key_id" in config_dict and config_dict["select_key_id"]:
            sel_id = str(config_dict["select_key_id"]).strip()
            for k in saved_keys:
                if k.get("id") == sel_id:
                    k["last_used"] = _now_ist_iso()
                    sel_key = k.get("key", "")
                    current["gemini_api_key"] = sel_key
                    gem_cur["api_key"] = sel_key
                    current["active_key_source"] = "models_config.json"
                    current["active_key_suffix"] = f"...{sel_key[-4:]}" if len(sel_key) >= 4 else sel_key
                    break

        # 3. New raw key passed
        if "gemini_api_key" in config_dict:
            val = str(config_dict["gemini_api_key"]).strip()
            if val and "****" not in val:
                gem_cur["api_key"] = val
                current["gemini_api_key"] = val
                current["active_key_source"] = "models_config.json"
                current["active_key_suffix"] = f"...{val[-4:]}" if len(val) >= 4 else val
                existing = next((k for k in saved_keys if k.get("key") == val), None)
                if existing:
                    existing["last_used"] = _now_ist_iso()
                else:
                    sfx = f"...{val[-4:]}" if len(val) >= 4 else val
                    label = config_dict.get("key_label") or f"Account ({sfx})"
                    saved_keys.insert(0, {
                        "id": f"key_{uuid.uuid4().hex[:8]}",
                        "label": label,
                        "key": val,
                        "added_at": _now_ist_iso(),
                        "last_used": _now_ist_iso(),
                    })
            elif val == "" and not config_dict.get("select_key_id"):
                gem_cur["api_key"] = ""
                current["gemini_api_key"] = ""
                current["active_key_suffix"] = "(not set)"

        if "gemini_model" in config_dict:
            gem_cur["default_model"] = str(config_dict["gemini_model"]).strip()
            current["gemini_model"] = gem_cur["default_model"]

        if "gemini" in config_dict and isinstance(config_dict["gemini"], dict):
            for k, v in config_dict["gemini"].items():
                if k == "api_key":
                    if "gemini_api_key" not in config_dict and not config_dict.get("select_key_id"):
                        val = str(v).strip()
                        if val and "****" not in val:
                            gem_cur["api_key"] = val
                            current["gemini_api_key"] = val
                        elif val == "":
                            gem_cur["api_key"] = ""
                            current["gemini_api_key"] = ""
                elif k == "default_model":
                    gem_cur["default_model"] = str(v).strip()
                    current["gemini_model"] = gem_cur["default_model"]
                elif k in ("enabled", "timeout_seconds"):
                    gem_cur[k] = v

        current["saved_gemini_keys"] = saved_keys
        current["saved_keys"] = saved_keys

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        logger.info("Saved AI model configuration to %s", config_path)
        return current

    @classmethod
    def test_ollama_connection(
        cls,
        base_url: str | None = None,
        model: str | None = None,
        engine_root: Path | str | None = None,
    ) -> dict[str, Any]:
        """Test connection to an Ollama instance by querying /api/tags."""
        clean_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        url = f"{clean_url}/api/tags"

        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "SPD-Analysis-Engine"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                return {
                    "success": True,
                    "message": f"Connected to Ollama at {clean_url}.",
                    "models": models,
                    "installed_models": models,
                    "model_found": (model in models) if model else True,
                }
        except urllib.error.URLError as url_err:
            return {
                "success": False,
                "message": f"Could not connect to Ollama at {clean_url} ({url_err.reason}). Ensure 'ollama serve' is running.",
                "models": [],
                "installed_models": [],
            }
        except Exception as exc:
            return {
                "success": False,
                "message": f"Error connecting to Ollama: {exc}",
                "models": [],
                "installed_models": [],
            }

    @classmethod
    def test_gemini_connection(
        cls,
        api_key: str | None = None,
        key_id: str | None = None,
        model: str | None = None,
        engine_root: Path | str | None = None,
    ) -> dict[str, Any]:
        """Test Gemini API connection by querying the models list endpoint."""
        clean_key = ""
        if key_id:
            cfg = cls.load_config(engine_root)
            for k in cfg.get("saved_gemini_keys", []):
                if k.get("id") == key_id:
                    clean_key = str(k.get("key", "")).strip()
                    break

        if not clean_key and api_key and "****" not in api_key:
            clean_key = api_key.strip()

        if not clean_key:
            cfg = cls.load_config(engine_root)
            clean_key = str(cfg.get("gemini_api_key", "")).strip()

        if not clean_key or "__REPLACE_" in clean_key:
            return {
                "success": False,
                "message": "Gemini API key is required to test connection.",
                "models": [],
            }

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={clean_key}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "SPD-Analysis-Engine"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_models = data.get("models", [])
                models = [
                    m["name"].replace("models/", "")
                    for m in raw_models
                    if "gemini" in m.get("name", "").lower()
                ]
                return {
                    "success": True,
                    "message": "Gemini API connection successful! Valid Gemini API key.",
                    "models": models[:12],
                }
        except urllib.error.HTTPError as http_err:
            err_body = ""
            try:
                err_body = http_err.read().decode("utf-8")
            except Exception:
                pass
            scrubbed = err_body.replace(clean_key, "***") if clean_key else err_body
            msg = f"HTTP {http_err.code}"
            try:
                err_json = json.loads(scrubbed)
                msg = err_json.get("error", {}).get("message") or msg
            except Exception:
                pass
            return {
                "success": False,
                "message": f"Gemini connection failed ({http_err.code}): {msg}",
                "models": [],
            }
        except Exception as exc:
            scrubbed_exc = str(exc).replace(clean_key, "***") if clean_key else str(exc)
            return {
                "success": False,
                "message": f"Error connecting to Gemini API: {scrubbed_exc}",
                "models": [],
            }

    # -----------------------------------------------------------------------
    # Diff Caching & Chunking
    # -----------------------------------------------------------------------

    def compute_diff_hash(self, diff_text: str) -> str:
        """Compute a deterministic SHA-256 hash of normalized diff content."""
        normalized = diff_text.strip().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def get_cached_analysis(
        self,
        diff_hash: str,
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Check if an analysis for this diff hash already exists in disk cache."""
        return self.cache_mgr.get(
            diff_hash=diff_hash,
            project_name=project_name,
            session_id=session_id,
            burst_id=burst_id,
        )

    def save_cached_analysis(
        self,
        diff_hash: str,
        analysis: dict[str, Any],
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> None:
        """Persist analysis result to disk cache."""
        self.cache_mgr.save(
            diff_hash=diff_hash,
            analysis=analysis,
            project_name=project_name,
            session_id=session_id,
            burst_id=burst_id,
        )

    def chunk_diff(self, diff_text: str, max_lines: int | None = None, max_chars: int = 30000) -> str:
        """
        Intelligently chunk/truncate diffs exceeding max_lines or max_chars to prevent
        exceeding model context or wasting tokens/TPM limits.
        Preserves diff and file headers while truncating internal file diff bodies.
        """
        if max_lines is None:
            max_lines = self._config.get("analysis", {}).get("max_diff_lines_for_summary", 250)

        file_chunks = [c for c in re.split(r"(?=^diff --git |^--- )", diff_text, flags=re.MULTILINE) if c.strip()]

        if len(file_chunks) > 1 and (len(diff_text) > max_chars or len(diff_text.splitlines()) > max_lines):
            processed_chunks = []
            per_file_chars = max(1000, max_chars // len(file_chunks))
            per_file_lines = max(10, max_lines // len(file_chunks))
            for chunk in file_chunks:
                c_lines = chunk.splitlines()
                headers = [l for l in c_lines if l.startswith(('diff ', 'index ', '---', '+++', '@@'))]
                body_lines = [l for l in c_lines if not l.startswith(('diff ', 'index ', '---', '+++', '@@'))]

                if len(body_lines) > per_file_lines or len(chunk) > per_file_chars:
                    keep = max(3, per_file_lines // 2)
                    head_body = body_lines[:keep]
                    tail_body = body_lines[-keep:] if len(body_lines) > keep * 2 else []
                    omitted_lines = len(body_lines) - (len(head_body) + len(tail_body))
                    new_chunk = (
                        "\n".join(headers + head_body)
                        + f"\n... [{omitted_lines} lines of internal diff omitted for analysis] ...\n"
                        + "\n".join(tail_body)
                    )
                    processed_chunks.append(new_chunk)
                else:
                    processed_chunks.append(chunk)
            truncated = "\n".join(processed_chunks)
        else:
            lines = diff_text.splitlines()
            if len(lines) > max_lines:
                half = max_lines // 2
                head = lines[:half]
                tail = lines[-half:]
                omitted = len(lines) - (len(head) + len(tail))
                truncated = (
                    "\n".join(head)
                    + f"\n\n... [{omitted} lines of unified diff omitted for analysis] ...\n\n"
                    + "\n".join(tail)
                )
            else:
                truncated = diff_text

        # Character budget guard: ensure total length <= max_chars
        if len(truncated) > max_chars:
            margin = 400
            head_len = (max_chars - margin) // 2
            tail_len = head_len
            truncated = (
                truncated[:head_len]
                + f"\n\n... [diff truncated from {len(truncated)} to {max_chars} chars to respect API TPM limits] ...\n\n"
                + truncated[-tail_len:]
            )

        return truncated

    # -----------------------------------------------------------------------
    # Prompt Construction
    # -----------------------------------------------------------------------

    def _build_prompt(
        self,
        diff_text: str,
        first_file: str | None = None,
        summary: str | None = None,
        files: list[str] | None = None,
        project_name: str | None = None,
        workspace_path: Path | str | None = None,
        tech_stack: str | None = None,
        dir_tree: str | None = None,
        previous_summary: str | dict[str, Any] | None = None,
        executed_commands: list[str] | None = None,
    ) -> str:
        topology_parts = []
        if project_name:
            topology_parts.append(f"Project: {project_name}")
        if workspace_path:
            topology_parts.append(f"Workspace: {workspace_path}")
        if tech_stack:
            topology_parts.append(f"Tech Stack: {tech_stack}")
        topology_header = " | ".join(topology_parts) if topology_parts else "Project Topology"

        tree_block = f"\nDirectory Tree (2-level overview):\n`` `\n{dir_tree}\n`` `\n" if dir_tree else ""

        prev_text = ""
        if previous_summary:
            if isinstance(previous_summary, dict):
                prev_text = previous_summary.get("intent") or str(previous_summary.get("summary") or "")
            else:
                prev_text = str(previous_summary).strip()
        chronological_block = f"\nPrevious Burst Context:\n\"{prev_text}\"\n" if prev_text else ""

        exec_block = f"\nExecuted Commands:\n" + "\n".join(f"- `{c}`" for c in executed_commands) + "\n" if executed_commands else ""

        file_info = f"Files touched: {', '.join(files)}" if files else ""
        entry_info = f"Primary entrypoint: {first_file}" if first_file else ""
        context_info = f"Burst Summary: {summary}" if summary else ""
        meta = " | ".join(filter(None, [file_info, entry_info, context_info]))

        return f"""You are an expert software architect and code auditor analyzing an atomic code edit burst.
Analyze the provided unified diff, executed commands, and project context, and generate a concise, high-value technical explanation in valid JSON format.

{topology_header}
{tree_block}{chronological_block}{exec_block}
Metadata:
{meta}

Unified Diff:
`` `diff
{diff_text}
`` `

Instructions:
You MUST respond with ONLY a valid, parseable JSON object without markdown fences, matching this exact schema:
{{
  "intent": "<High-level architectural intent explaining WHY this change happened and what problem/task it addresses>",
  "execution_rationale": "<Technical rationale explaining why the terminal commands were executed in relation to the code changes>",
  "architecture_impact": "<How this change alters component structure, state management, dependencies, or API flow>",
  "key_modifications": [
    "<Concise bullet point detailing specific structural, function, class, or route changes>",
    "<Next specific structural modification>"
  ],
  "functionality_gained": "<Concrete technical explanation of new capabilities, fixes, or behavioral guarantees achieved by these changes>"
}}
"""

    def _parse_llm_json(self, raw_text: str) -> dict[str, Any]:
        """Strip markdown fences and parse structured JSON from LLM output."""
        cleaned = raw_text.strip()
        # Remove markdown code blocks if wrapped
        if cleaned.startswith("`` `"):
            cleaned = re.sub(r"^`` `(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*`` `$", "", cleaned)
            cleaned = cleaned.strip()

        # Extract first JSON object if surrounded by preamble
        json_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(1)

        data = json.loads(cleaned)
        # Validate schema shape
        intent = str(data.get("intent") or data.get("commit_title") or "Code edit burst analyzed.")
        arch_impact = str(data.get("architecture_impact") or "Component structure and data flow maintained.")
        exec_rationale = str(data.get("execution_rationale") or data.get("rationale") or "Terminal commands executed during validation and runtime verification.")

        mods_raw = data.get("key_modifications") or data.get("summary") or data.get("commit_body") or []
        if isinstance(mods_raw, str):
            mods = [s.strip("- ") for s in mods_raw.splitlines() if s.strip()] or [mods_raw]
        elif isinstance(mods_raw, list):
            mods = [str(x) for x in mods_raw]
        else:
            mods = ["Code changes applied."]

        gains = str(data.get("functionality_gained", "Enhanced functionality."))

        result = dict(data)
        result["intent"] = intent
        result["execution_rationale"] = exec_rationale
        result["architecture_impact"] = arch_impact
        result["key_modifications"] = mods
        result["summary"] = mods  # backward compatibility alias
        result["functionality_gained"] = gains
        return result

    # -----------------------------------------------------------------------
    # Provider Implementations
    # -----------------------------------------------------------------------

    def reload_config(self) -> dict[str, Any]:
        """Reload configuration from disk or environment."""
        self._config = self.load_config(self.engine_root)
        return self._config

    def _call_ollama(self, prompt: str) -> dict[str, Any]:
        """Issue HTTP POST to local Ollama instance."""
        if self.config_path.exists():
            self._config = self.load_config(self.engine_root)
        ollama_cfg = self._config.get("ollama", {})
        base_url = ollama_cfg.get("base_url", "http://localhost:11434").rstrip("/")
        model = ollama_cfg.get("default_model", "qwen2.5-coder:7b")
        timeout = ollama_cfg.get("timeout_seconds", 60)
        options = ollama_cfg.get("options", {})

        url = f"{base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": options,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode("utf-8")
            resp_json = json.loads(resp_body)
            raw_response = resp_json.get("response", "")
            parsed = self._parse_llm_json(raw_response)
            parsed["provider"] = f"ollama:{model}"
            return parsed

    def _call_gemini(self, prompt: str) -> dict[str, Any]:
        """Issue API call to Gemini using official Google GenAI SDK or HTTPS fallback with rate-limit retry."""
        if self.config_path.exists():
            self._config = self.load_config(self.engine_root)
        gemini_cfg = self._config.get("gemini", {})
        api_key = gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key or "__REPLACE_" in api_key:
            raise ValueError("Gemini API key is not configured")

        model_name = gemini_cfg.get("default_model", "gemini-3.1-flash-lite")
        # Ensure deprecated / zero-quota models are upgraded to active quota tier
        if any(m in model_name for m in ["gemini-1.5", "gemini-2.0"]):
            model_name = "gemini-3.1-flash-lite"

        max_retries = 3
        backoff_delays = [3.0, 7.0, 15.0]

        for attempt in range(max_retries):
            # Check rate limiter on initial call and pause if cooling down
            if attempt == 0:
                self.rate_limiter.wait_if_needed(max_wait_s=5.0)
            try:
                # Try Google GenAI SDK first
                try:
                    from google import genai
                    from google.genai import types

                    client = genai.Client(api_key=api_key)
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=gemini_cfg.get("options", {}).get("temperature", 0.2),
                            tools=[],
                        ),
                    )
                    raw_text = resp.text or ""
                    parsed = self._parse_llm_json(raw_text)
                    parsed["provider"] = f"gemini:{model_name}"
                    return parsed
                except ImportError:
                    logger.debug("google.genai SDK not imported; using HTTPS REST fallback.")
                except Exception as sdk_exc:
                    err_str = str(sdk_exc)
                    err_lower = err_str.lower()
                    if "spending cap" in err_lower:
                        self.rate_limiter.trigger_cooldown(60.0)
                        raise
                    if any(k in err_lower for k in ("429", "503", "resource_exhausted", "quota exceeded", "high demand", "unavailable", "service unavailable")):
                        if attempt == max_retries - 1 and ("429" in err_lower or "resource_exhausted" in err_lower):
                            self.rate_limiter.trigger_cooldown(60.0)
                        raise
                    logger.warning("google.genai SDK call failed: %s; trying HTTPS REST fallback.", sdk_exc)

                # HTTPS REST Fallback
                timeout = gemini_cfg.get("timeout_seconds", 30)
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.2,
                    },
                }
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    resp_body = resp.read().decode("utf-8")
                    resp_json = json.loads(resp_body)
                    candidates = resp_json.get("candidates", [])
                    if not candidates:
                        raise RuntimeError("Gemini returned empty candidates")
                    part = candidates[0].get("content", {}).get("parts", [{}])[0]
                    raw_text = part.get("text", "")
                    parsed = self._parse_llm_json(raw_text)
                    parsed["provider"] = f"gemini-rest:{model_name}"
                    return parsed

            except urllib.error.HTTPError as http_err:
                if http_err.code in (429, 503):
                    if attempt < max_retries - 1:
                        sleep_s = backoff_delays[attempt]
                        logger.warning(
                            "Gemini API rate/capacity limit HTTP %d encountered. Retrying in %.0fs (attempt %d/%d)...",
                            http_err.code, sleep_s, attempt + 1, max_retries,
                        )
                        time.sleep(sleep_s)
                        continue
                    else:
                        if http_err.code == 429:
                            self.rate_limiter.trigger_cooldown(60.0)
                raise
            except Exception as exc:
                err_str = str(exc)
                err_lower = err_str.lower()
                if "spending cap" in err_lower or "api_key_invalid" in err_lower or "api key not valid" in err_lower:
                    self.rate_limiter.trigger_cooldown(60.0)
                    raise
                if any(k in err_lower for k in ("429", "503", "resource_exhausted", "quota exceeded", "high demand", "unavailable", "service unavailable")):
                    if attempt < max_retries - 1:
                        sleep_s = backoff_delays[attempt]
                        logger.warning(
                            "Gemini API rate/capacity limit (%s) encountered. Retrying in %.0fs (attempt %d/%d)...",
                            err_str[:80], sleep_s, attempt + 1, max_retries,
                        )
                        time.sleep(sleep_s)
                        continue
                    else:
                        if "429" in err_lower or "resource_exhausted" in err_lower:
                            self.rate_limiter.trigger_cooldown(60.0)
                raise

    # -----------------------------------------------------------------------
    # Fallback Generation
    # -----------------------------------------------------------------------

    def _fallback_analysis(
        self,
        files: list[str],
        first_file: str | None,
        summary: str | None,
        reason: str = "offline",
        project_name: str | None = None,
        workspace_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Generate a meaningful heuristic fallback analysis when AI is unreachable."""
        f_count = len(files)
        entry_txt = f" starting at '{first_file}'" if first_file else ""
        desc = summary or f"Burst modification affecting {f_count} file(s)"

        summary_bullets = [
            f"Atomic file modification detected across {f_count} file(s){entry_txt}.",
            f"Event details: {desc}",
        ]
        if files:
            summary_bullets.append(f"Target files: {', '.join(files[:6])}{' ...' if len(files) > 6 else ''}")

        return {
            "intent": f"Code modification bundle ({desc}). [AI offline fallback]",
            "execution_rationale": "Terminal commands executed during development workflow to verify changes in workspace context.",
            "architecture_impact": f"Component modifications committed to project '{project_name or 'workspace'}' without regression.",
            "key_modifications": summary_bullets,
            "summary": summary_bullets,
            "functionality_gained": "Modifications captured into ACID SQLite WAL storage, unified diff cache, and isolated Shadow Git micro-versioning.",
            "provider": "offline-fallback",
            "fallback_reason": reason,
            "cached": False,
        }

    # -----------------------------------------------------------------------
    # Public API: analyze_burst & analyze_event
    # -----------------------------------------------------------------------

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
        Supports both signatures:
          analyze_burst(project_name, workspace_path, event, previous_summary=...)
          analyze_burst(event_dict, patches, provider=...)
        """
        # Parse positional arguments for flexible signature compatibility
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

        # Strict Diff Sanitization: Strip noise/temporary artifacts
        sanitized_patches = [
            p for p in patches
            if is_sanitized_patch(p.get("file_path", ""))
        ]

        files = [p.get("file_path", "") for p in sanitized_patches if p.get("file_path")]
        first_file = event.get("first_file_touched")
        summary = event.get("summary")

        # 1. Aggregate sanitized diff content
        diff_blocks = []
        for p in sanitized_patches:
            fp = p.get("file_path", "unknown")
            diff = p.get("diff_content") or ""
            diff_blocks.append(f"diff --git a/{fp} b/{fp}\n{diff}")

        full_diff = "\n\n".join(diff_blocks).strip()

        # Check if isolated ShadowGit has recorded an atomic micro-commit delta for this burst
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

        # 2. Check Cache
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

        # 3. Deep Context Synthesis: Directory Tree & Tech Stack & Executed Commands
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

        # 4. Chunk diff & Build deep prompt
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

        # 5. Resolve provider order
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

        # 6. Fallback if all providers failed
        if not result:
            result = self._fallback_analysis(
                files, first_file, summary,
                reason=f"Providers unreachable ({last_error})",
                project_name=project_name,
                workspace_path=workspace_path,
            )

        result["cached"] = False
        result["diff_hash"] = diff_hash

        # 7. Save valid results to cache
        self.save_cached_analysis(
            diff_hash,
            result,
            project_name=p_name,
            session_id=sess_id,
            burst_id=burst_num,
        )
        return result

    analyze_event = analyze_burst

    # -----------------------------------------------------------------------
    # Public API: generate_session_changelog
    # -----------------------------------------------------------------------

    def _fallback_changelog(
        self,
        files: list[str],
        events: list[dict[str, Any]],
        reason: str = "offline",
    ) -> dict[str, Any]:
        """Generate a structured heuristic Conventional Commit & CHANGELOG when AI is offline."""
        ts_str = _now_ist_header()

        # Guess scope from files
        scope = "core"
        if any(f.startswith("web/") or f.endswith((".html", ".css", ".js")) for f in files):
            scope = "web" if not any("scripts/" in f or f.endswith(".py") for f in files) else "fullstack"
        elif any(f.startswith("tests/") for f in files):
            scope = "test"

        action = "feat" if any("create" in str(e.get("summary", "")).lower() or "add" in str(e.get("summary", "")).lower() for e in events) else "refactor"
        if any("fix" in str(e.get("summary", "")).lower() or "error" in str(e.get("summary", "")).lower() for e in events):
            action = "fix"

        f_count = len(files)
        commit_title = f"{action}({scope}): synchronize session changes across {f_count} file(s)"

        bullets = []
        for e in events:
            s = e.get("summary")
            if s and s not in bullets:
                bullets.append(f"- {s}")
        if not bullets:
            bullets = [f"- Modified {f}" for f in files[:8]]

        body_text = "\n".join(bullets[:10])

        file_list_md = "\n".join(f"- `{f}`" for f in files[:12])
        if len(files) > 12:
            file_list_md += f"\n- ... and {len(files) - 12} other files"

        changelog_md = f"""### [{ts_str}] {commit_title}

#### Key Highlights
{body_text}

#### Files Modified
{file_list_md}

#### Functional & Architectural Impact
Captured atomic multi-file prompt burst edits into session database and repository working tree. [Synthesized via offline fallback: {reason}]"""

        return {
            "commit_title": commit_title,
            "commit_body": body_text,
            "changelog_entry": changelog_md,
            "provider": "offline-fallback",
            "fallback_reason": reason,
            "cached": False,
        }

    def generate_session_changelog(
        self,
        session_events: list[dict[str, Any]],
        diffs: list[dict[str, Any]] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """
        Analyze all prompt bursts and diffs across a monitoring session to produce
        a Conventional Commit title, description, and markdown changelog entry.

        Parameters
        ----------
        session_events : list[dict]
            List of event dictionaries recorded during the session.
        diffs : list[dict] | None
            Optional explicit list of patch dictionaries. If omitted, extracted
            from session_events.
        provider : str | None
            Optional AI provider override ('heuristic', 'ollama', 'gemini').

        Returns
        -------
        dict[str, Any]
            ``{"commit_title": str, "commit_body": str, "changelog_entry": str, "provider": str, "cached": bool}``
        """
        ts_str = _now_ist_header()

        # 1. Gather all files and diff contents
        files_set: set[str] = set()
        diff_snippets: list[str] = []

        all_patches = list(diffs or [])
        for ev in session_events:
            for p in ev.get("patches", []):
                all_patches.append(p)

        for p in all_patches:
            fp = p.get("file_path")
            if fp:
                files_set.add(fp)
            diff = p.get("diff_content")
            if diff:
                diff_snippets.append(f"--- {fp} ---\n{diff}")

        files_list = sorted(list(files_set))
        if not files_list:
            return self._fallback_changelog([], session_events, reason="No files modified in session")

        # 2. Check Cache using session diff hash
        aggregated_diff = "\n\n".join(diff_snippets).strip()
        session_hash = "session_" + self.compute_diff_hash(aggregated_diff or ", ".join(files_list))
        cached = self.get_cached_analysis(session_hash)
        if cached:
            return cached

        # 3. Build Prompt
        event_summaries = [f"- Burst #{e.get('id', '?')}: {e.get('summary', 'burst edit')}" for e in session_events if e.get("event_type") == "EDIT"]
        event_text = "\n".join(event_summaries[:15]) or "- Multi-file prompt burst edits"

        chunked_diffs = self.chunk_diff(aggregated_diff, max_lines=250) if aggregated_diff else "Diff not available"

        prompt = f"""You are a senior software architect and release engineer writing a Conventional Commit and CHANGELOG entry for a software session.
Analyze the following session event history, modified files, and diff excerpts to generate a high quality release summary.

Session Metadata:
- Timestamp: {ts_str}
- Files touched: {', '.join(files_list[:25])}
- Event progression:
{event_text}

Diff Excerpts:
`` `diff
{chunked_diffs}
`` `

Instructions:
Respond with ONLY a valid, parseable JSON object without markdown fences, matching this exact schema:
{{
  "commit_title": "<Conventional commit message under 72 chars with type and scope, e.g. feat(core): implement user authentication and oauth2 flow>",
  "commit_body": "<Bulleted list of high-level modifications grouped by component>",
  "changelog_entry": "### [{ts_str}] <commit_title>\\n\\n#### Key Highlights\\n- <bullet 1>\\n- <bullet 2>\\n\\n#### Files Modified\\n{', '.join(files_list[:10])}\\n\\n#### Functional & Architectural Impact\\n<Detailed paragraph explaining concrete capabilities gained, bugs resolved, or behavioral guarantees achieved.>"
}}
"""

        # 4. Resolve providers
        cfg = self.load_config(self.engine_root)
        pref_provider = (provider or cfg.get("preferred_provider") or cfg.get("default_provider", "gemini")).lower()

        if pref_provider in ("heuristic", "none"):
            fallback = self._fallback_changelog(files_list, session_events, reason="heuristic_mode")
            fallback["provider"] = "heuristic"
            self.save_cached_analysis(session_hash, fallback)
            return fallback

        has_gemini = bool(cfg.get("gemini", {}).get("api_key") or os.environ.get("GEMINI_API_KEY"))

        providers = []
        if pref_provider == "gemini" and has_gemini:
            providers = ["gemini", "ollama"]
        elif pref_provider == "ollama":
            providers = ["ollama", "gemini"] if has_gemini else ["ollama"]
        else:
            providers = ["gemini"] if has_gemini else ["ollama"]

        result = None
        last_error = ""

        for prov in providers:
            try:
                if prov == "gemini":
                    rate_status = self.rate_limiter.get_status()
                    if rate_status["cooling_down"] and rate_status["cooldown_remaining_s"] > 3.0:
                        logger.info("Gemini is cooling down (%.1fs remaining); skipping changelog to next provider.", rate_status["cooldown_remaining_s"])
                        last_error = f"Gemini cooling down ({rate_status['cooldown_remaining_s']}s remaining)"
                        continue
                    logger.info("Generating session changelog via Gemini API...")
                    raw_res = self._call_gemini(prompt)
                elif prov == "ollama":
                    logger.info("Generating session changelog via local Ollama...")
                    raw_res = self._call_ollama(prompt)
                else:
                    continue

                # Ensure result conforms to changelog schema
                commit_title = str(raw_res.get("commit_title") or raw_res.get("intent") or "feat: session code updates").strip()
                if not any(commit_title.startswith(p) for p in ("feat", "fix", "refactor", "chore", "docs", "style", "test", "perf")):
                    commit_title = f"feat(core): {commit_title}"
                commit_body = raw_res.get("commit_body")
                if isinstance(commit_body, list):
                    commit_body = "\n".join(f"- {b}" for b in commit_body)
                elif not commit_body:
                    commit_body = "\n".join(f"- {s}" for s in raw_res.get("summary", ["Applied code modifications."]))

                changelog_entry = raw_res.get("changelog_entry")
                if not changelog_entry:
                    changelog_entry = f"### [{ts_str}] {commit_title}\n\n#### Key Highlights\n{commit_body}\n\n#### Files Modified\n" + "\n".join(f"- `{f}`" for f in files_list[:10]) + f"\n\n#### Functional & Architectural Impact\n{raw_res.get('functionality_gained', 'Enhanced functionality.')}"

                result = {
                    "commit_title": commit_title,
                    "commit_body": commit_body,
                    "changelog_entry": changelog_entry,
                    "provider": raw_res.get("provider", prov),
                    "cached": False,
                }
                break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Provider %s failed for changelog: %s", prov, exc)

        if not result:
            result = self._fallback_changelog(files_list, session_events, reason=f"Providers unreachable ({last_error})")

        result["diff_hash"] = session_hash
        self.save_cached_analysis(session_hash, result)
        return result

    # -----------------------------------------------------------------------
    # Public API: explain_command
    # -----------------------------------------------------------------------

    def _fallback_command_explanation(
        self,
        command_str: str,
        exit_code: int = 0,
        duration_s: float = 0.0,
        reason: str = "offline",
    ) -> dict[str, Any]:
        """Generate structured heuristic command intent, technical purpose, and outcome analysis."""
        cmd_clean = command_str.strip()
        tokens = cmd_clean.split()
        first_token = os.path.basename(tokens[0]).lower() if tokens else ""

        intent = "Execute CLI tool or terminal command."
        technical_purpose = f"Invoked '{first_token}' with {len(tokens) - 1} parameter(s)."

        # Specific heuristics for common developer tools
        if first_token in ("git", "git.exe"):
            subcmd = tokens[1].lower() if len(tokens) > 1 else ""
            if subcmd == "status":
                intent = "Inspect working directory and staging area status."
                technical_purpose = "Git CLI query checking for modified, untracked, or staged changes without modifying repository state."
            elif subcmd == "commit":
                intent = "Record staged snapshot into project Git version history."
                technical_purpose = "Git commit operation creating a new revision object with author metadata and commit message."
            elif subcmd == "diff":
                intent = "Inspect working tree or branch code line modifications."
                technical_purpose = "Git diff comparator outputting unified diff hunks."
            elif subcmd == "push":
                intent = "Publish local Git commits to remote upstream repository."
                technical_purpose = "Git network push transferring packfiles and updating remote refs."
            elif subcmd in ("branch", "checkout", "switch"):
                intent = "Manage or switch workspace active Git branch."
                technical_purpose = f"Git branch operation ({subcmd}) adjusting HEAD reference."
            else:
                intent = f"Git version control command ({subcmd or 'general'})."
                technical_purpose = f"Invoked Git subcommand '{subcmd}' to manipulate or query repository state."

        elif first_token in ("python", "python3", "py", "python.exe"):
            if " -c " in f" {cmd_clean} ":
                intent = "Execute inline Python snippet or one-liner evaluation."
                technical_purpose = "Python runtime evaluation via '-c' flag for ad-hoc script execution."
            elif "-m unittest" in cmd_clean:
                intent = "Execute unit test suite using Python unittest framework."
                technical_purpose = "Python test discovery and test runner execution."
            elif "-m py_compile" in cmd_clean:
                intent = "Syntax verification and bytecode pre-compilation."
                technical_purpose = "Python py_compile module validating script syntax without full execution."
            else:
                script_name = tokens[1] if len(tokens) > 1 and not tokens[1].startswith("-") else "script"
                intent = f"Run Python program '{os.path.basename(script_name)}'."
                technical_purpose = f"Python interpreter invoked to execute '{script_name}'."

        elif first_token in ("pytest", "pytest.exe"):
            intent = "Execute automated test suite with pytest test runner."
            technical_purpose = "Pytest runner executing assertions, fixtures, and regression tests."

        elif first_token in ("npm", "yarn", "pnpm", "npx"):
            subcmd = tokens[1].lower() if len(tokens) > 1 else ""
            if subcmd in ("test", "t"):
                intent = "Run project test suite defined in package.json."
                technical_purpose = "Node.js package manager triggering the 'test' npm lifecycle script."
            elif subcmd in ("run", "start", "dev"):
                intent = f"Start application or execute script '{tokens[2] if len(tokens) > 2 else subcmd}'."
                technical_purpose = f"Node package manager running configured build/dev lifecycle script."
            elif subcmd in ("install", "i", "add"):
                intent = "Install project dependencies or packages."
                technical_purpose = "Package dependency resolution, lockfile synchronization, and node_modules extraction."
            else:
                intent = f"Node.js package manager operation ({subcmd})."
                technical_purpose = f"Invoked {first_token} with subcommand '{subcmd}'."

        elif first_token in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
            if any(f in cmd_clean.lower() for f in ("-command", "-c ", " -c\"")):
                intent = "Execute automated PowerShell script block or headless command."
                technical_purpose = "PowerShell host running non-interactive headless script command."
            else:
                intent = "Execute interactive or scripting PowerShell directive."
                technical_purpose = "PowerShell shell runtime processing command expression."

        elif first_token in ("cmd", "cmd.exe"):
            intent = "Execute Windows command processor directive."
            technical_purpose = "Windows cmd.exe shell processing batch/CLI arguments."

        # Outcome analysis
        if exit_code == 0:
            outcome_analysis = f"Process terminated successfully (exit code 0) in {duration_s:.2f}s."
        else:
            outcome_analysis = f"Process failed or returned non-zero status (exit code {exit_code}) after {duration_s:.2f}s."

        summary = [
            f"Command: {cmd_clean[:70]}{'...' if len(cmd_clean) > 70 else ''}",
            f"Execution time: {duration_s:.2f}s | Result: {'Success' if exit_code == 0 else f'Failed (code {exit_code})'}",
        ]

        return {
            "intent": intent,
            "technical_purpose": technical_purpose,
            "outcome_analysis": outcome_analysis,
            "summary": summary,
            "provider": f"heuristic ({reason})",
            "cached": False,
        }

    def explain_command(
        self,
        project_name: str,
        command_str: str,
        exit_code: int = 0,
        duration_s: float = 0.0,
        surrounding_burst_context: list[dict[str, Any]] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """
        Explain a terminal command execution using AI (Gemini or Ollama) with offline fallback.

        Returns JSON matching schema:
        {
          "intent": str,
          "technical_purpose": str,
          "outcome_analysis": str,
          "summary": list[str],
          "provider": str,
          "cached": bool
        }
        """
        clean_cmd = command_str.strip()
        cache_key = "cmd_" + hashlib.sha256(f"{clean_cmd}_{exit_code}_{duration_s:.1f}".encode()).hexdigest()[:24]

        if not clean_cmd:
            res = self._fallback_command_explanation(command_str, exit_code, duration_s, reason="empty_command")
            res["diff_hash"] = cache_key
            return res

        # 1. Check Cache
        cached = self.get_cached_analysis(cache_key, project_name=project_name)
        if cached:
            return cached

        # 2. Check Provider Preferences
        cfg = self.load_config(self.engine_root)
        pref_provider = (provider or cfg.get("preferred_provider") or cfg.get("default_provider", "gemini")).lower()

        if pref_provider in ("heuristic", "none"):
            fallback = self._fallback_command_explanation(clean_cmd, exit_code, duration_s, reason="heuristic_mode")
            fallback["provider"] = "heuristic"
            fallback["diff_hash"] = cache_key
            self.save_cached_analysis(cache_key, fallback, project_name=project_name)
            return fallback

        # 3. Build Prompt
        context_str = ""
        if surrounding_burst_context:
            c_lines = [
                f"- #{c.get('id', '?')} [{c.get('event_type', 'EVENT')}]: {c.get('first_file_touched') or c.get('summary', '')}"
                for c in surrounding_burst_context[:5]
            ]
            context_str = f"\nSurrounding Workspace Events:\n" + "\n".join(c_lines)

        prompt = f"""You are a senior DevOps, systems architecture, and security engineer.
Analyze the following terminal command execution recorded during a software development session.

Project: {project_name}
Command: {clean_cmd}
Exit Code: {exit_code}
Execution Duration: {duration_s:.2f} seconds
{context_str}

Instructions:
Respond with ONLY a valid, parseable JSON object without markdown fences, matching this exact schema:
{{
  "intent": "<Clear, plain-English statement of why this command was invoked and what the developer or automated agent was attempting to achieve>",
  "technical_purpose": "<Specific explanation of the binary, compiler, interpreter, flags, and system interaction>",
  "outcome_analysis": "<Assessment of the execution result based on exit code {exit_code} and runtime {duration_s:.2f}s>",
  "summary": [
    "<Highlight bullet 1>",
    "<Highlight bullet 2>"
  ]
}}
"""

        has_gemini = bool(cfg.get("gemini", {}).get("api_key") or os.environ.get("GEMINI_API_KEY"))
        providers = []
        if pref_provider == "gemini" and has_gemini:
            providers = ["gemini", "ollama"]
        elif pref_provider == "ollama":
            providers = ["ollama", "gemini"] if has_gemini else ["ollama"]
        else:
            providers = ["gemini"] if has_gemini else ["ollama"]

        result = None
        last_error = ""

        for prov in providers:
            try:
                if prov == "gemini":
                    rate_status = self.rate_limiter.get_status()
                    if rate_status["cooling_down"] and rate_status["cooldown_remaining_s"] > 3.0:
                        logger.info("Gemini cooling down (%.1fs remaining); skipping to next provider.", rate_status["cooldown_remaining_s"])
                        last_error = f"Gemini cooling down ({rate_status['cooldown_remaining_s']}s remaining)"
                        continue
                    logger.info("Explaining command via Gemini API...")
                    raw_res = self._call_gemini(prompt)
                elif prov == "ollama":
                    logger.info("Explaining command via local Ollama...")
                    raw_res = self._call_ollama(prompt)
                else:
                    continue

                intent = str(raw_res.get("intent") or "Command executed").strip()
                tech_purpose = str(raw_res.get("technical_purpose") or raw_res.get("architecture_impact") or "CLI tool execution").strip()
                outcome = str(raw_res.get("outcome_analysis") or f"Terminated with exit code {exit_code} in {duration_s:.2f}s").strip()
                summary_raw = raw_res.get("summary")
                if isinstance(summary_raw, list):
                    summary = [str(s) for s in summary_raw]
                elif isinstance(summary_raw, str):
                    summary = [summary_raw]
                else:
                    summary = [f"Ran {clean_cmd[:50]}", f"Exit: {exit_code} ({duration_s:.2f}s)"]

                result = {
                    "intent": intent,
                    "technical_purpose": tech_purpose,
                    "outcome_analysis": outcome,
                    "summary": summary,
                    "provider": raw_res.get("provider", prov),
                    "cached": False,
                }
                break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Provider %s failed for command explanation: %s", prov, exc)

        if not result:
            result = self._fallback_command_explanation(clean_cmd, exit_code, duration_s, reason=f"Providers unreachable ({last_error})")

        result["diff_hash"] = cache_key
        self.save_cached_analysis(cache_key, result, project_name=project_name)
        return result
```

---

### [5/27] File: `scripts\diff_calc.py`
- **Lines:** 340 | **Size:** 10.64 KB | **Type:** py

```python
"""
scripts/diff_calc.py
====================
Unified diff calculator and baseline cache manager for the SPD Analysis Engine.

Classes
-------
DiffCalculator
    Maintains an in-memory and disk-backed baseline of tracked files in
    ``current/<project_slug>/baseline/``. Computes standard unified diffs
    using Python's ``difflib`` and detects entrypoint edit files.
"""

from __future__ import annotations

import difflib
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Directory names to ignore during scanning and baseline capture
DEFAULT_IGNORED_DIRS: set[str] = {
    ".git",
    ".sf",
    ".sfdx",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "patches",
    "baseline",
    ".mypy_cache",
    ".pytest_cache",
}

#: File patterns / names to ignore during baseline capture
DEFAULT_IGNORED_EXTENSIONS: set[str] = {
    ".tmp",
    ".swp",
    ".swo",
    ".pyc",
    ".pyo",
    ".bak",
    ".log",
    ".db",
    ".db-shm",
    ".db-wal",
    ".pid",
}

DEFAULT_IGNORED_FILES: set[str] = {
    "session.db",
    "session.db-shm",
    "session.db-wal",
    "worker.log",
    "worker.pid",
    "worker.pid.stale",
    "worker.status.json",
    "project.meta",
}


def is_binary_file(file_path: Path, block_size: int = 8192) -> bool:
    """
    Heuristically determine if a file is binary by looking for null bytes
    or decoding errors in the first *block_size* bytes.
    """
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(block_size)
            if b"\x00" in chunk:
                return True
            try:
                chunk.decode("utf-8")
            except UnicodeDecodeError:
                return True
        return False
    except OSError:
        return True


class DiffCalculator:
    """
    Manages file baseline snapshots and computes unified diffs.

    Parameters
    ----------
    baseline_dir : Path | str
        Directory where snapshot files are stored (``current/<slug>/baseline/``).
    target_dir : Path | str
        Root directory of the workspace being monitored.
    """

    def __init__(
        self,
        baseline_dir: Path | str,
        target_dir: Path | str,
        ignored_dirs: set[str] | None = None,
        ignored_extensions: set[str] | None = None,
    ) -> None:
        self.baseline_dir = Path(baseline_dir).resolve()
        self.target_dir = Path(target_dir).resolve()
        self.ignored_dirs = ignored_dirs or DEFAULT_IGNORED_DIRS
        self.ignored_extensions = ignored_extensions or DEFAULT_IGNORED_EXTENSIONS

        # In-memory baseline cache: { "rel/path/to/file.py": "file content..." }
        self._cache: dict[str, str] = {}

        # Ensure baseline directory exists on disk
        self.baseline_dir.mkdir(parents=True, exist_ok=True)

    def is_ignored(self, path: Path | str) -> bool:
        """
        Check if a file or directory path should be ignored.
        """
        p = Path(path)
        parts = p.parts
        for part in parts:
            if part in self.ignored_dirs or part.startswith(".sf"):
                return True
        name = p.name
        if name in DEFAULT_IGNORED_FILES:
            return True
        if name.startswith("catalog.json") or name.endswith(".__staging__") or name.endswith(".tmp"):
            return True
        if p.suffix.lower() in self.ignored_extensions:
            return True
        return False

    def normalize_rel_path(self, path: Path | str) -> str:
        """
        Convert an absolute or relative path to a normalized relative path
        (POSIX-style forward slashes) against ``target_dir``.
        """
        p = Path(path)
        if p.is_absolute():
            try:
                rel = p.relative_to(self.target_dir)
            except ValueError:
                # Path is outside target_dir, return normalized str
                return str(p).replace("\\", "/")
        else:
            rel = p
        return str(rel).replace("\\", "/")

    def capture_baseline(self, target_dir: Path | str | None = None) -> dict[str, str]:
        """
        Take an initial snapshot of all text files in ``target_dir``.

        Skips binary files, directories matching ignore filters, and hidden tooling files.
        Populates both in-memory cache and disk files in ``baseline_dir``.

        Returns
        -------
        dict[str, str]
            Mapping of relative file path -> file content.
        """
        root = Path(target_dir).resolve() if target_dir else self.target_dir
        if not root.exists():
            logger.warning("Target directory does not exist for baseline capture: %s", root)
            return {}

        self._cache.clear()

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored directories in-place
            dirnames[:] = [d for d in dirnames if d not in self.ignored_dirs and not d.startswith(".sf")]

            for fname in filenames:
                file_path = Path(dirpath) / fname
                if self.is_ignored(file_path):
                    continue

                if is_binary_file(file_path):
                    logger.debug("Skipping binary file in baseline: %s", file_path)
                    continue

                rel_path = self.normalize_rel_path(file_path)
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    self._cache[rel_path] = content

                    # Persist to baseline_dir on disk
                    disk_target = self.baseline_dir / Path(rel_path)
                    disk_target.parent.mkdir(parents=True, exist_ok=True)
                    disk_target.write_text(content, encoding="utf-8")
                except OSError as exc:
                    logger.warning("Failed to read file for baseline %s: %s", file_path, exc)

        logger.info(
            "Captured baseline with %d text files in %s",
            len(self._cache), self.baseline_dir,
        )
        return dict(self._cache)

    def get_baseline(self, rel_path: str) -> str | None:
        """
        Retrieve the baseline content for a file.
        Checks in-memory cache first, then disk baseline file.
        Returns ``None`` if file was not in the baseline (e.g. newly created file).
        """
        norm = self.normalize_rel_path(rel_path)
        if norm in self._cache:
            return self._cache[norm]

        disk_path = self.baseline_dir / Path(norm)
        if disk_path.is_file():
            try:
                content = disk_path.read_text(encoding="utf-8", errors="replace")
                self._cache[norm] = content
                return content
            except OSError:
                return None
        return None

    def update_baseline(self, rel_path: str, new_content: str | None) -> None:
        """
        Update the baseline for a file after a burst is processed.

        If *new_content* is ``None``, the file has been deleted and is
        evicted from cache and disk.
        """
        norm = self.normalize_rel_path(rel_path)
        disk_path = self.baseline_dir / Path(norm)

        if new_content is None:
            self._cache.pop(norm, None)
            if disk_path.exists():
                try:
                    disk_path.unlink()
                except OSError as exc:
                    logger.debug("Could not remove baseline file %s: %s", disk_path, exc)
        else:
            self._cache[norm] = new_content
            try:
                disk_path.parent.mkdir(parents=True, exist_ok=True)
                disk_path.write_text(new_content, encoding="utf-8")
            except OSError as exc:
                logger.warning("Could not persist updated baseline file %s: %s", disk_path, exc)

    def compute_diff(
        self,
        file_path: str,
        old_content: str | None = None,
        new_content: str | None = None,
    ) -> str:
        """
        Compute standard unified diff string with file headers and line numbers.

        Parameters
        ----------
        file_path : str
            Relative or absolute file path.
        old_content : str | None
            Previous content (retrieved from baseline if ``None``).
        new_content : str | None
            Current content on disk (read from disk if ``None``).

        Returns
        -------
        str
            Unified diff formatted string, or empty string if no diff.
        """
        norm = self.normalize_rel_path(file_path)

        if old_content is None:
            old_content = self.get_baseline(norm) or ""

        if new_content is None:
            abs_path = self.target_dir / Path(norm)
            if abs_path.is_file():
                try:
                    if is_binary_file(abs_path):
                        return ""
                    new_content = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    new_content = ""
            else:
                # File deleted
                new_content = ""

        if old_content == new_content:
            return ""

        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        from_file = f"a/{norm}" if old_content else "/dev/null"
        to_file = f"b/{norm}" if new_content else "/dev/null"

        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=from_file,
                tofile=to_file,
                lineterm="",
            )
        )

        if not diff_lines:
            return ""

        return "\n".join(diff_lines) + "\n"

    @staticmethod
    def detect_first_file(changes_list: list[dict[str, Any]]) -> str | None:
        """
        Analyze timestamps of modified files within a burst to pinpoint
        the earliest touched file (the AI entrypoint file).

        Parameters
        ----------
        changes_list : list[dict]
            List of event dicts containing at least:
            ``{"file_path": ..., "timestamp": float}`` or
            ``{"file_path": ..., "ts": ...}``.

        Returns
        -------
        str | None
            The relative or absolute file_path of the earliest event.
        """
        if not changes_list:
            return None

        def _sort_key(item: dict[str, Any]) -> float:
            if "timestamp" in item and isinstance(item["timestamp"], (int, float)):
                return float(item["timestamp"])
            if "time" in item and isinstance(item["time"], (int, float)):
                return float(item["time"])
            return 0.0

        sorted_changes = sorted(changes_list, key=_sort_key)
        return str(sorted_changes[0].get("file_path", ""))
```

---

### [6/27] File: `scripts\event_bundler.py`
- **Lines:** 362 | **Size:** 12.89 KB | **Type:** py

```python
"""
scripts/event_bundler.py
========================
Thread-safe sliding-window event aggregator for the SPD Analysis Engine.

Classes
-------
EventBundler
    Coalesces rapid streams of file system I/O notifications into atomic
    event bundles using a configurable sliding debounce window (default 3.5s).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    """Return current IST timestamp in ISO-8601 format."""
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


_utcnow_iso = _now_ist_iso

_SUPPRESSED_PATHS_LOCK = threading.Lock()
_SUPPRESSED_PATHS: dict[str, float] = {}


def suppress_file_path(file_path: str, duration_s: float = 4.0) -> None:
    """Suppress watchdog events for a specific file path or filename for duration_s seconds."""
    norm = str(file_path).replace("\\", "/").lower().strip("/")
    base = Path(file_path).name.lower()
    expiry = time.monotonic() + duration_s
    with _SUPPRESSED_PATHS_LOCK:
        _SUPPRESSED_PATHS[norm] = expiry
        if base:
            _SUPPRESSED_PATHS[base] = expiry


def is_path_suppressed(file_path: str) -> bool:
    """Check if file_path is currently under temporary suppression window."""
    norm = str(file_path).replace("\\", "/").lower().strip("/")
    base = Path(file_path).name.lower()
    now = time.monotonic()
    with _SUPPRESSED_PATHS_LOCK:
        expired = [k for k, exp in _SUPPRESSED_PATHS.items() if exp < now]
        for k in expired:
            del _SUPPRESSED_PATHS[k]
        return (_SUPPRESSED_PATHS.get(norm, 0.0) > now) or (_SUPPRESSED_PATHS.get(base, 0.0) > now)


class EventBundler:
    """
    Sliding-window event aggregator with a quiet-period debounce.

    Whenever an event arrives, the 3.5-second countdown is reset.
    When 3.5 seconds elapse with no new file system activity, the bundle
    is flushed and dispatched to the registered callback.

    Parameters
    ----------
    callback : Callable[[dict[str, Any]], None]
        Function to invoke with the flushed bundle payload.
    quiet_period : float, optional
        Inactivity threshold in seconds before flushing (default: 3.5).
    """

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], None] | None = None,
        quiet_period: float = 3.5,
        on_activity: Callable[[], None] | None = None,
    ) -> None:
        self.callback = callback or (lambda b: None)
        self.quiet_period = quiet_period
        self.on_activity = on_activity

        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending_events: dict[str, dict[str, Any]] = {}  # file_path -> event_dict
        self._first_event_mono: float | None = None
        self._first_event_iso: str | None = None
        self._stopped = False
        self._recent_actions: deque[dict[str, Any]] = deque(maxlen=50)

    def suppress_path(self, file_path: str, duration_s: float = 4.0) -> None:
        """Temporarily suppress events on file_path (e.g. self-triggering CHANGELOG.md)."""
        suppress_file_path(file_path, duration_s)

    def record_action(
        self,
        action_type: str,
        file_path: str,
        details: str = "",
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        # Filter out internal IDE background tasks and git credential helpers
        combined_text = f"{file_path} {details}".lower()
        for pattern in (
            "shellintegration.ps1",
            "jsonservermain",
            "eslintserver",
            "typescript-language-features",
            "apex-language-server",
            "git-credential-",
        ):
            if pattern in combined_text:
                return {}

        ts_str = datetime.now(_IST).strftime("%I:%M:%S %p IST")
        action = {
            "timestamp": ts_str,
            "iso": _utcnow_iso(),
            "action_type": action_type.upper(),
            "file_path": file_path,
            "details": details,
        }
        with self._lock:
            self._recent_actions.append(action)
        return action

    def get_recent_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return rolling recent actions in chronological order."""
        with self._lock:
            actions = list(self._recent_actions)
        return actions[-limit:] if limit > 0 else actions

    @property
    def is_pending(self) -> bool:
        """Return True if events are queued awaiting quiet period expiry."""
        with self._lock:
            return bool(self._pending_events)

    def get_debounce_status(self) -> dict[str, Any]:
        """
        Return live debounce countdown status for UI progress meters.
        """
        with self._lock:
            if not self._pending_events or self._timer is None or self._stopped:
                return {
                    "active": False,
                    "remaining_s": 0.0,
                    "elapsed_s": 0.0,
                    "files_count": 0,
                    "first_file": None,
                    "quiet_period": self.quiet_period,
                }
            now = time.monotonic()
            last_event = max((e["last_seen_mono"] for e in self._pending_events.values()), default=now)
            elapsed = now - last_event
            remaining = max(0.0, self.quiet_period - elapsed)
            first_file = next(iter(self._pending_events.keys()), None)
            return {
                "active": True,
                "remaining_s": round(remaining, 2),
                "elapsed_s": round(elapsed, 2),
                "files_count": len(self._pending_events),
                "first_file": first_file,
                "quiet_period": self.quiet_period,
            }

    def add_event(
        self,
        event_type: str,
        file_path: str,
        timestamp: float | None = None,
    ) -> None:
        """
        Record a file system event and reset the sliding debounce window.

        Parameters
        ----------
        event_type : str
            Type of file operation (e.g. "modified", "created", "deleted").
        file_path : str
            Workspace-relative or absolute path to the file.
        timestamp : float | None
            Event occurrence timestamp (monotonic). Defaults to ``time.monotonic()``.
        """
        if is_path_suppressed(file_path):
            logger.debug("Suppressing event on '%s' (self-trigger suppression active)", file_path)
            return

        now_mono = time.monotonic() if timestamp is None else timestamp
        now_iso = _utcnow_iso()

        if self.on_activity:
            try:
                self.on_activity()
            except Exception:
                pass

        with self._lock:
            if self._stopped:
                logger.warning("EventBundler is stopped; dropping event for %s", file_path)
                return

            if self._first_event_mono is None:
                self._first_event_mono = now_mono
                self._first_event_iso = now_iso

            # Track or update file in pending events
            if file_path not in self._pending_events:
                self._pending_events[file_path] = {
                    "file_path": file_path,
                    "event_type": event_type,
                    "first_seen_mono": now_mono,
                    "first_seen_iso": now_iso,
                    "last_seen_mono": now_mono,
                    "edit_count": 1,
                }
            else:
                entry = self._pending_events[file_path]
                entry["last_seen_mono"] = now_mono
                entry["edit_count"] += 1
                # If it was created and later modified, keep original creation intent
                if entry["event_type"] != "created":
                    entry["event_type"] = event_type

            # Reset sliding debounce timer
            if self._timer is not None:
                self._timer.cancel()

            self._timer = threading.Timer(self.quiet_period, self._on_timer_fired)
            self._timer.daemon = True
            self._timer.start()

            self._recent_actions.append({
                "timestamp": datetime.now(_IST).strftime("%I:%M:%S %p IST"),
                "iso": now_iso,
                "action_type": "WRITE",
                "file_path": file_path,
                "details": f"File {event_type}",
            })

            logger.debug(
                "Added event %s for '%s' (pending files: %d, timer reset to %.1fs)",
                event_type, file_path, len(self._pending_events), self.quiet_period,
            )

    def _on_timer_fired(self) -> None:
        """Invoked when the quiet period elapses with zero new I/O events."""
        bundle = self._extract_bundle()
        if bundle is not None:
            logger.info(
                "Quiet period (%.1fs) elapsed — flushing bundle with %d file(s) (entrypoint: %s)",
                self.quiet_period, len(bundle["files_touched"]), bundle["first_file"],
            )
            self._dispatch_bundle(bundle)

    def _extract_bundle(self) -> dict[str, Any] | None:
        """
        Thread-safely harvest and clear all pending events.
        Returns the constructed bundle dict or None if empty.
        """
        with self._lock:
            if not self._pending_events:
                return None

            now_mono = time.monotonic()
            first_mono = self._first_event_mono or now_mono
            duration = max(0.0, now_mono - first_mono)

            # Sort pending events by first_seen_mono to identify entrypoint file
            sorted_entries = sorted(
                self._pending_events.values(),
                key=lambda x: x["first_seen_mono"],
            )
            first_file = sorted_entries[0]["file_path"] if sorted_entries else None
            files_touched = [entry["file_path"] for entry in sorted_entries]

            bundle: dict[str, Any] = {
                "event_type": "EDIT",
                "files_touched": files_touched,
                "first_file": first_file,
                "duration_s": round(duration, 2),
                "events": list(sorted_entries),
                "started_at": self._first_event_iso,
                "flushed_at": _utcnow_iso(),
            }

            # Reset internal state
            self._pending_events.clear()
            self._first_event_mono = None
            self._first_event_iso = None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

            self._recent_actions.append({
                "timestamp": datetime.now(_IST).strftime("%I:%M:%S %p IST"),
                "iso": _utcnow_iso(),
                "action_type": "BURST",
                "file_path": first_file or "workspace",
                "details": f"Bundled {len(files_touched)} files",
            })

            return bundle

    def _dispatch_bundle(self, bundle: dict[str, Any]) -> None:
        """Execute the user callback outside of the lock."""
        try:
            self.callback(bundle)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in EventBundler callback: %s", exc)

    def flush_burst_now(self) -> dict[str, Any] | None:
        """
        Immediately seal and flush any unbundled file modifications in the debounce
        queue into a completed burst, resetting the timer to idle (Lap button).
        Returns the emitted bundle or None if no pending events.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        bundle = self._extract_bundle()
        if bundle is not None:
            logger.info("Manual burst sealed (Lap button) — emitting bundle with %d file(s)", len(bundle["files_touched"]))
            self._dispatch_bundle(bundle)
            return bundle
        return None

    def flush(self) -> None:
        """
        Immediately flush any pending events regardless of timer.
        Useful during worker shutdown or explicit synchronisation points.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        bundle = self._extract_bundle()
        if bundle is not None:
            logger.info("Explicit flush of pending bundle with %d file(s)", len(bundle["files_touched"]))
            self._dispatch_bundle(bundle)

    def stop(self) -> None:
        """
        Stop the bundler, cancelling any active timers and flushing pending events.
        """
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        bundle = self._extract_bundle()
        if bundle is not None:
            self._dispatch_bundle(bundle)
```

---

### [7/27] File: `scripts\git_shadow.py`
- **Lines:** 1085 | **Size:** 41.59 KB | **Type:** py

```python
"""
scripts/git_shadow.py
=====================
Shadow Git micro-versioning for the SPD Analysis Engine (Phase 4).

Manages an isolated Git repository in ``current/<slug>/shadow_git/`` pointing to
the workspace ``target_dir`` via ``--git-dir`` and ``--work-tree``.

Features
--------
1. Complete isolation: Operates independently from the project's actual
   ``.git`` folder (if one exists). Zero conflicts with the user's branches or remotes.
2. Micro-commit per burst: Creates an atomic commit for each debounced prompt
   burst, capturing the exact multi-file diff state and entrypoint file.
3. Commit history: Provides programmatic retrieval of commit hashes, timestamps,
   and commit messages for auditing and diff navigation.
4. Auto-scaffold: Automatically configures local git identity (``SPD Shadow Engine``)
   so commits never fail on unconfigured developer machines.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
IGNORED_PATH_PARTS = {".sf", ".sfdx", ".git", "node_modules", "__pycache__"}

HARDENED_GITIGNORE_TEMPLATE = """# Environment & Secrets
.env
.env.*
*.key
*.pem
*.token
*id_rsa*
credentials.json
git_config.json

# Salesforce & IDE Noise
.sf/
.sfdx/
*catalog.json*
*.__staging__
*.tmp
.vscode/
.idea/

# Python
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
.venv/
env/
*.egg-info/

# Node & Web
node_modules/
.npm/
dist/
.next/
.cache/

# SPD Engine Files
.spd/
session.db*
*.patch
worker.log
*.meta
"""


try:
    from scripts.event_bundler import suppress_file_path
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.event_bundler import suppress_file_path
    except (ImportError, ModuleNotFoundError):
        def suppress_file_path(path: Any, duration_s: float = 5.0) -> None:
            pass


def ensure_hardened_gitignore(target_path: Path | str) -> bool:
    """
    Check if .gitignore exists in the target workspace root. If missing, automatically
    create it with comprehensive protection for environment secrets, IDE noise, and engine files.
    """
    target = Path(target_path).resolve()
    gi_path = target / ".gitignore"
    if not gi_path.exists():
        try:
            target.mkdir(parents=True, exist_ok=True)
            suppress_file_path(str(gi_path), duration_s=5.0)
            gi_path.write_text(HARDENED_GITIGNORE_TEMPLATE, encoding="utf-8")
            suppress_file_path(str(gi_path), duration_s=5.0)
            logger.info("Automatically generated hardened .gitignore in %s", target)
            return True
        except Exception as exc:
            logger.warning("Could not create hardened .gitignore in %s: %s", target, exc)
            return False
    return False


def scrub_tokens(text: str, token: str | None = None) -> str:
    """Sanitize passwords, personal access tokens, and API credentials from strings/URLs."""
    if not text:
        return ""
    # 1. URL basic auth: https://user:pass@host -> https://***:***@host
    s = re.sub(r'(https?://)([^:\s@]+):([^@\s]+)@', r'\1***:***@', text)
    # 2. URL single token auth: https://token@host -> https://***@host
    s = re.sub(r'(https?://)([^@/:\s]+)@', r'\1***@', s)
    # 3. GitHub personal access tokens: ghp_...
    s = re.sub(r'ghp_[A-Za-z0-9_]+', 'ghp_***', s)
    # 4. GitHub Fine-grained PATs: github_pat_...
    s = re.sub(r'github_pat_[A-Za-z0-9_]+', 'github_pat_***', s)
    # 5. Google / Gemini API keys: AIza...
    s = re.sub(r'AIza[A-Za-z0-9_\-]+', 'AIza***', s)
    # 6. Explicit token if provided
    if token and token in s:
        s = s.replace(token, '***')
    return s


def discover_native_git_remote(target_path: Path | str) -> tuple[str, str]:
    """
    Auto-discover remote origin URL and active branch from target workspace's native .git.
    Returns (remote_url, branch_name).
    """
    target = Path(target_path).resolve()
    git_dir = target / ".git"
    if not git_dir.exists():
        return "", ""

    git_bin = shutil.which("git")
    if not git_bin:
        return "", ""

    remote_url = ""
    branch = ""

    try:
        res = subprocess.run(
            [git_bin, "config", "--get", "remote.origin.url"],
            cwd=str(target),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            creationflags=_NO_WINDOW_FLAG,
            shell=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            remote_url = scrub_tokens(res.stdout.strip())
    except Exception:
        pass

    try:
        res_branch = subprocess.run(
            [git_bin, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(target),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            creationflags=_NO_WINDOW_FLAG,
            shell=False,
        )
        if res_branch.returncode == 0 and res_branch.stdout.strip():
            branch = res_branch.stdout.strip()
    except Exception:
        pass

    return remote_url, branch or "main"


def is_salesforce_or_transient_file(path_str: str) -> bool:
    """Check if a file belongs to .sf, .sfdx, staging, or transient directories."""
    p = Path(path_str)
    for part in p.parts:
        if part in IGNORED_PATH_PARTS or part.startswith(".sf"):
            return True
    name = p.name
    if name.startswith("catalog.json") or name.endswith(".__staging__") or name.endswith(".tmp"):
        return True
    return False


class ShadowGit:
    """
    Manages an isolated Git repository for passive session micro-versioning.

    Parameters
    ----------
    shadow_dir : Path | str
        Path to the isolated shadow git directory (e.g. ``current/<slug>/shadow_git``).
    target_dir : Path | str
        Path to the workspace root directory being monitored.
    """

    def __init__(self, shadow_dir: Path | str, target_dir: Path | str) -> None:
        self.shadow_dir = Path(shadow_dir).resolve()
        self.target_dir = Path(target_dir).resolve()
        self.git_cmd = shutil.which("git")
        self._lock = threading.Lock()
        self._initialized = False

    @property
    def is_available(self) -> bool:
        """Return True if git binary is present on the host system."""
        return self.git_cmd is not None

    def _run_git(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Execute a git command targeting the shadow repository."""
        if not self.git_cmd:
            raise RuntimeError("Git executable not found in PATH")

        cmd = [
            self.git_cmd,
            f"--git-dir={self.shadow_dir}",
            f"--work-tree={self.target_dir}",
            *args,
        ]

        # Use clean environment without interfering GIT_* vars
        env = dict(os.environ)
        env.pop("GIT_DIR", None)
        env.pop("GIT_WORK_TREE", None)
        env.pop("GIT_INDEX_FILE", None)

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
            creationflags=_NO_WINDOW_FLAG,
            shell=False,
        )

    def init_repo(self) -> bool:
        """
        Initialize the shadow git repository and configure local identity.
        Returns True on success, False if git is unavailable.
        """
        if not self.is_available:
            logger.warning("Git is not installed; ShadowGit micro-versioning disabled.")
            return False

        with self._lock:
            try:
                ensure_hardened_gitignore(self.target_dir)
                self.shadow_dir.mkdir(parents=True, exist_ok=True)
                # Check if repository already initialized
                head_file = self.shadow_dir / "HEAD"
                if not head_file.exists():
                    res = self._run_git("init")
                    if res.returncode != 0:
                        logger.error("Failed to init shadow git: %s", res.stderr)
                        return False

                    # Configure identity inside shadow repository only
                    self._run_git("config", "user.name", "SPD Shadow Engine")
                    self._run_git("config", "user.email", "spd-shadow@local")
                    self._run_git("config", "core.autocrlf", "false")
                    self._run_git("config", "commit.gpgsign", "false")

                    logger.info("Initialized shadow git repo at %s", self.shadow_dir)

                # Set up shadow git info/exclude to ignore .sf and transient artifacts
                try:
                    exclude_file = self.shadow_dir / "info" / "exclude"
                    exclude_file.parent.mkdir(parents=True, exist_ok=True)
                    default_excludes = "\n.sf\n.sf/**\n.sfdx\n.sfdx/**\n*.__staging__\n*.tmp\ncatalog.json*\nsession.db*\nworker.log\nworker.pid*\nworker.status.json\nproject.meta\n"
                    if exclude_file.exists():
                        cur = exclude_file.read_text(encoding="utf-8", errors="replace")
                        if ".sf" not in cur:
                            exclude_file.write_text(cur + default_excludes, encoding="utf-8")
                    else:
                        exclude_file.write_text(default_excludes, encoding="utf-8")
                except Exception as exc:
                    logger.debug("Failed to write shadow exclude file: %s", exc)

                self._initialized = True
                return True
            except Exception as exc:
                logger.error("Error initializing shadow git: %s", exc)
                return False

    def commit_burst(
        self,
        event_id: int,
        first_file: str | None,
        summary: str,
        files_touched: list[str] | None = None,
    ) -> str | None:
        """
        Stage touched files and create an atomic micro-commit.

        Parameters
        ----------
        event_id : int
            Event identifier from ``session.db``.
        first_file : str | None
            The earliest modified file in the burst (entrypoint).
        summary : str
            Brief human-readable summary of the burst.
        files_touched : list[str] | None
            Relative paths of modified or created files.

        Returns
        -------
        str | None
            The full commit SHA-1 hash, or ``None`` on failure.
        """
        if not self.is_available:
            return None

        if not self._initialized and not self.init_repo():
            return None

        with self._lock:
            try:
                # Stage files
                if files_touched:
                    # Filter out .sf, .sfdx, and transient files
                    files_touched = [f for f in files_touched if not is_salesforce_or_transient_file(f)]
                    # Filter existing files vs deleted files
                    existing = []
                    deleted = []
                    for f in files_touched:
                        abs_p = self.target_dir / Path(f)
                        if abs_p.exists():
                            existing.append(f)
                        else:
                            deleted.append(f)

                    if existing:
                        self._run_git("add", "--", *existing)
                    if deleted:
                        self._run_git("rm", "--cached", "--ignore-unmatch", "--", *deleted)
                else:
                    self._run_git("add", "-A")

                entry_tag = f"[{first_file}] " if first_file else ""
                commit_msg = f"Burst #{event_id}: {entry_tag}{summary}"

                res = self._run_git("commit", "-m", commit_msg, "--allow-empty")
                if res.returncode != 0 and "nothing to commit" not in res.stdout:
                    logger.debug("Shadow commit notice: %s", res.stdout or res.stderr)

                # Get latest commit hash
                hash_res = self._run_git("rev-parse", "HEAD")
                if hash_res.returncode == 0:
                    commit_hash = hash_res.stdout.strip()
                    logger.info("Shadow Git micro-commit created: %s (Burst #%d)", commit_hash[:8], event_id)
                    return commit_hash
                return None
            except Exception as exc:
                logger.error("Failed to create shadow git commit: %s", exc)
                return None

    def get_commit_history(self, max_count: int = 50) -> list[dict[str, Any]]:
        """
        Retrieve chronological commit history from the shadow git repository.

        Returns
        -------
        list[dict]
            List of ``{"hash", "short_hash", "author", "date", "message"}`` dicts.
        """
        if not self.is_available or not self.shadow_dir.exists():
            return []

        with self._lock:
            try:
                res = self._run_git(
                    "log",
                    f"-n{max_count}",
                    "--format=%H|%an|%ad|%s",
                    "--date=iso-strict",
                )
                if res.returncode != 0:
                    return []

                commits = []
                for line in res.stdout.splitlines():
                    parts = line.strip().split("|", 3)
                    if len(parts) == 4:
                        commits.append({
                            "hash": parts[0],
                            "short_hash": parts[0][:8],
                            "author": parts[1],
                            "date": parts[2],
                            "message": parts[3],
                        })
                return commits
            except Exception as exc:
                logger.debug("Error fetching shadow git history: %s", exc)
                return []

    def get_commit_for_event(self, event_id: int) -> str | None:
        """Find the ShadowGit commit hash matching the given burst event ID."""
        if not self.is_available or not self.shadow_dir.exists():
            return None
        with self._lock:
            try:
                res = self._run_git("log", "--all", f"--grep=^Burst #{event_id}:", "-n1", "--format=%H")
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
                return None
            except Exception as exc:
                logger.debug("Error finding commit for event %d: %s", event_id, exc)
                return None

    def rollback_burst_commit(self, event_id: int, commit_hash: str | None = None) -> bool:
        """
        Rollback a Shadow Git micro-commit for a given burst event.
        If the commit is HEAD, performs `git reset --hard HEAD~1`.
        If older, resets to `<commit>~1` or reverts.
        Returns True if successful, False otherwise.
        """
        if not self.is_available or not self.shadow_dir.exists():
            return False

        with self._lock:
            try:
                commit = commit_hash or self.get_commit_for_event(event_id)
                if not commit:
                    logger.debug("No ShadowGit commit found for event %d", event_id)
                    return False

                # Check current HEAD
                head_res = self._run_git("rev-parse", "HEAD")
                current_head = head_res.stdout.strip() if head_res.returncode == 0 else ""

                # Check if commit has a parent
                has_parent_res = self._run_git("rev-parse", "--verify", f"{commit}~1")
                has_parent = (has_parent_res.returncode == 0)

                if current_head.startswith(commit):
                    if has_parent:
                        res = self._run_git("reset", "--hard", "HEAD~1")
                    else:
                        res = self._run_git("update-ref", "-d", "HEAD")
                    if res.returncode != 0:
                        return False
                    verify_head = self._run_git("rev-parse", "HEAD")
                    new_head = verify_head.stdout.strip() if verify_head.returncode == 0 else ""
                    return (new_head != current_head) or (not has_parent and not new_head)
                else:
                    if has_parent:
                        res = self._run_git("revert", "--no-edit", commit)
                        if res.returncode != 0:
                            self._run_git("revert", "--abort")
                            res = self._run_git("reset", "--hard", f"{commit}~1")
                        return res.returncode == 0
                    else:
                        res = self._run_git("reset", "--hard", "HEAD~1")
                        return res.returncode == 0
            except Exception as exc:
                logger.error("Failed to rollback shadow git commit for event %d: %s", event_id, exc)
                return False

    def get_delta_diff(
        self,
        file_path: str,
        commit_hash: str | None = None,
        event_id: int | None = None,
    ) -> str:
        """
        Compute the dedicated version delta diff for a file relative to its immediate previous commit.
        Runs `git diff <commit>~1 <commit> -- <file_path>` (or diff-tree for root commits).
        """
        if not self.is_available or not self.shadow_dir.exists():
            return ""

        norm_path = str(file_path).replace("\\", "/")

        with self._lock:
            try:
                ref = commit_hash
                if not ref and event_id is not None:
                    res = self._run_git("log", "--all", f"--grep=^Burst #{event_id}:", "-n1", "--format=%H")
                    if res.returncode == 0 and res.stdout.strip():
                        ref = res.stdout.strip()

                if not ref:
                    ref = "HEAD"

                parent_check = self._run_git("rev-parse", "--verify", f"{ref}^")
                if parent_check.returncode == 0:
                    diff_res = self._run_git("diff", f"{ref}~1", ref, "--", norm_path)
                else:
                    diff_res = self._run_git("diff-tree", "-p", "--root", ref, "--", norm_path)

                if diff_res.returncode == 0 and diff_res.stdout.strip():
                    return diff_res.stdout.strip() + "\n"
                return ""
            except Exception as exc:
                logger.debug("Error computing delta diff in ShadowGit: %s", exc)
                return ""

    def get_burst_delta_diff(
        self,
        event_id: int | None = None,
        commit_hash: str | None = None,
        files: list[str] | None = None,
    ) -> str:
        """
        Compute delta diff strictly between immediate previous commit and current commit
        (`git diff HEAD~1 HEAD -- <files>`) for an atomic burst.
        """
        if not self.is_available or not self.shadow_dir.exists():
            return ""

        with self._lock:
            try:
                ref = commit_hash
                if not ref and event_id is not None:
                    res = self._run_git("log", "--all", f"--grep=^Burst #{event_id}:", "-n1", "--format=%H")
                    if res.returncode == 0 and res.stdout.strip():
                        ref = res.stdout.strip()

                if not ref:
                    ref = "HEAD"

                parent_check = self._run_git("rev-parse", "--verify", f"{ref}^")
                file_args = [str(f).replace("\\", "/") for f in (files or []) if f]
                cmd_args = ["--", *file_args] if file_args else []

                if parent_check.returncode == 0:
                    diff_res = self._run_git("diff", f"{ref}~1", ref, *cmd_args)
                else:
                    diff_res = self._run_git("diff-tree", "-p", "--root", ref, *cmd_args)

                if diff_res.returncode == 0 and diff_res.stdout.strip():
                    return diff_res.stdout.strip() + "\n"
                return ""
            except Exception as exc:
                logger.debug("Error computing burst delta diff in ShadowGit: %s", exc)
                return ""

    @staticmethod
    def ensure_hardened_gitignore(target_path: Path | str) -> bool:
        """Expose hardened gitignore utility as a static method on ShadowGit."""
        return ensure_hardened_gitignore(target_path)

    @classmethod
    def _ensure_shadow_repo(cls, repo_path: Path | str) -> bool:
        """
        Ensure target workspace root has a hardened .gitignore and valid repo configuration.
        """
        target = Path(repo_path).resolve()
        return ensure_hardened_gitignore(target)

    @staticmethod
    def init_primary_repo(target_dir: Path | str) -> bool:
        """
        Initialize a primary ``.git`` repository in the target workspace if absent.
        Automatically verifies and creates a hardened .gitignore.
        """
        target = Path(target_dir).resolve()
        ensure_hardened_gitignore(target)
        if (target / ".git").exists():
            return False

        git_bin = shutil.which("git")
        if not git_bin:
            return False

        try:
            res = subprocess.run(
                [git_bin, "init"],
                cwd=str(target),
                capture_output=True,
                text=True,
                check=False,
                creationflags=_NO_WINDOW_FLAG,
                shell=False,
            )
            if res.returncode == 0:
                logger.info("Initialized primary git repo in %s", target)
                return True
        except Exception as exc:
            logger.warning("Could not initialize primary git repo in %s: %s", target, exc)

        return False

    @staticmethod
    def push_to_remote(
        repo_path: Path | str,
        remote_url: str | None = None,
        commit_message: str | None = None,
        changelog_content: str | None = None,
        branch: str = "main",
        remote_name: str = "origin",
        engine_root: Path | str | None = None,
    ) -> dict[str, Any]:
        """
        Stage, commit, update CHANGELOG.md, and push primary workspace changes to a remote Git repository.

        Parameters
        ----------
        repo_path : Path | str
            Target workspace root directory containing the primary ``.git`` folder.
        remote_url : str | None
            Remote repository URL (e.g. ``https://github.com/owner/repo.git``) or remote name.
        commit_message : str | None
            Structured semantic commit message (e.g. Conventional Commit format).
        changelog_content : str | None
            Markdown content block to record in ``CHANGELOG.md``.
        branch : str
            Target branch name (default: ``"main"``).
        remote_name : str
            Remote identifier if remote_url is omitted (default: ``"origin"``).
        engine_root : Path | str | None
            Root of spd_analysis_engine for credential resolution.

        Returns
        -------
        dict[str, Any]
            ``{"success": bool, "message": str, "remote": str, "branch": str, "commit": str | None}``
        """
        target = Path(repo_path).resolve()
        git_bin = shutil.which("git")
        if not git_bin:
            return {
                "success": False,
                "message": "Git executable not found in PATH",
                "remote": remote_name,
                "branch": branch,
                "commit": None,
            }

        if not (target / ".git").exists():
            return {
                "success": False,
                "message": f"Target workspace '{target}' is not a Git repository (.git not found)",
                "remote": remote_name,
                "branch": branch,
                "commit": None,
            }

        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"

        def _run_in_repo(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [git_bin, *args],
                cwd=str(target),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
                creationflags=_NO_WINDOW_FLAG,
                shell=False,
            )

        try:
            # 1. Update CHANGELOG.md if content is provided
            if changelog_content and changelog_content.strip():
                try:
                    from spd_analysis_engine.scripts.event_bundler import suppress_file_path
                    suppress_file_path("CHANGELOG.md", 4.0)
                    suppress_file_path(str(target / "CHANGELOG.md"), 4.0)
                except Exception:
                    pass
                changelog_path = target / "CHANGELOG.md"
                cleaned_entry = changelog_content.strip()
                if changelog_path.exists():
                    existing_text = changelog_path.read_text(encoding="utf-8")
                    if existing_text.startswith("# Changelog"):
                        # Insert right below the header
                        rest = existing_text[len("# Changelog"):].lstrip()
                        new_text = f"# Changelog\n\n{cleaned_entry}\n\n{rest}"
                    else:
                        new_text = f"# Changelog\n\n{cleaned_entry}\n\n{existing_text}"
                else:
                    new_text = f"# Changelog\n\n{cleaned_entry}\n\n"
                changelog_path.write_text(new_text, encoding="utf-8")
                try:
                    from spd_analysis_engine.scripts.event_bundler import suppress_file_path
                    suppress_file_path("CHANGELOG.md", 4.0)
                    suppress_file_path(str(target / "CHANGELOG.md"), 4.0)
                except Exception:
                    pass
                logger.info("Updated CHANGELOG.md in %s", target)

            # 2. Detect active branch
            branch_res = _run_in_repo("branch", "--show-current")
            active_branch = branch_res.stdout.strip() if branch_res.returncode == 0 else ""
            if not active_branch or active_branch == "HEAD":
                active_branch = branch

            # 3. Stage changes
            _run_in_repo("add", "-A")

            # 4. Commit if working tree is dirty
            status_res = _run_in_repo("status", "--porcelain")
            if status_res.stdout.strip():
                from datetime import datetime, timezone
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                msg = commit_message.strip() if commit_message else f"SPD Session Sync: {ts}"
                commit_res = _run_in_repo("commit", "-m", msg, "--allow-empty")
                logger.info("Committed workspace changes: %s", commit_res.stdout.strip())

            # 5. Get current HEAD commit hash
            rev_res = _run_in_repo("rev-parse", "HEAD")
            commit_hash = rev_res.stdout.strip() if rev_res.returncode == 0 else None

            # 6. Resolve target push destination & credentials
            target_push = ""
            effective_remote_label = remote_name

            # Check if remote_url is provided and is a valid URL/path
            if remote_url and (remote_url.startswith("http://") or remote_url.startswith("https://") or Path(remote_url).exists() or remote_url.endswith(".git")):
                target_push = GitCredentialManager.get_authenticated_url(remote_url, engine_root=engine_root)
                effective_remote_label = remote_url
            else:
                lookup_name = remote_name or remote_url or "origin"
                remotes_res = _run_in_repo("remote")
                configured_remotes = [r.strip() for r in remotes_res.stdout.splitlines() if r.strip()]
                if lookup_name not in configured_remotes:
                    return {
                        "success": False,
                        "message": (
                            f"No remote named '{lookup_name}' configured in '{target}'. "
                            f"Configured remotes: {configured_remotes or 'none'}"
                        ),
                        "remote": lookup_name,
                        "branch": active_branch,
                        "commit": commit_hash,
                    }
                effective_remote_label = lookup_name
                # Get remote URL to inject credentials if it's HTTPS
                get_url_res = _run_in_repo("remote", "get-url", lookup_name)
                configured_url = get_url_res.stdout.strip()
                if configured_url.startswith("http://") or configured_url.startswith("https://"):
                    target_push = GitCredentialManager.get_authenticated_url(configured_url, engine_root=engine_root)
                else:
                    target_push = lookup_name

            # 7. Push to remote
            push_res = _run_in_repo("push", target_push, active_branch)
            clean_stdout = GitCredentialManager.scrub_text(push_res.stdout)
            clean_stderr = GitCredentialManager.scrub_text(push_res.stderr)

            if push_res.returncode == 0:
                masked_remote = GitCredentialManager.scrub_text(effective_remote_label)
                msg = f"Successfully pushed {active_branch} to {masked_remote}"
                logger.info(msg)
                return {
                    "success": True,
                    "message": msg,
                    "remote": masked_remote,
                    "branch": active_branch,
                    "commit": commit_hash,
                }
            else:
                err_msg = clean_stderr.strip() or clean_stdout.strip() or "Unknown git push error"
                logger.warning("Git push failed: %s", err_msg)
                return {
                    "success": False,
                    "message": f"Git push failed: {err_msg}",
                    "remote": GitCredentialManager.scrub_text(effective_remote_label),
                    "branch": active_branch,
                    "commit": commit_hash,
                }
        except Exception as exc:
            logger.exception("Exception during push_to_remote")
            return {
                "success": False,
                "message": f"Error executing git push: {GitCredentialManager.scrub_text(str(exc))}",
                "remote": GitCredentialManager.scrub_text(remote_name),
                "branch": branch,
                "commit": None,
            }


class GitCredentialManager:
    """
    Manages local GitHub credentials, PAT authentication, and connection testing.
    """

    @staticmethod
    def get_config_path(engine_root: Path | str | None = None) -> Path:
        if engine_root is None:
            engine_root = Path(__file__).resolve().parent.parent
        return Path(engine_root) / "ai_data" / "git_config.json"

    @classmethod
    def load_config(cls, engine_root: Path | str | None = None) -> dict[str, Any]:
        """Load GitHub settings from ai_data/git_config.json with default fallback."""
        path = cls.get_config_path(engine_root)
        defaults: dict[str, Any] = {
            "github_username": "",
            "github_token": "",
            "default_remote": "origin",
            "default_branch": "main",
            "auto_generate_changelog": True,
        }
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                defaults.update(data)
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", path, exc)
        return defaults

    @classmethod
    def save_config(cls, config_dict: dict[str, Any], engine_root: Path | str | None = None) -> dict[str, Any]:
        """Persist updated credentials and settings."""
        path = cls.get_config_path(engine_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        current = cls.load_config(engine_root)
        current.update(config_dict)
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        logger.info("Saved GitHub configuration to %s", path)
        return current

    @classmethod
    def mask_token(cls, token_or_cfg: str | dict[str, Any]) -> str | dict[str, Any]:
        """Mask token for display (e.g. ghp_****1234), or mask github_token in a config dict."""
        if isinstance(token_or_cfg, dict):
            masked_dict = dict(token_or_cfg)
            if "github_token" in masked_dict:
                masked_dict["github_token"] = cls.mask_token(str(masked_dict["github_token"]))
            return masked_dict

        token = str(token_or_cfg) if token_or_cfg is not None else ""
        if not token:
            return ""
        if len(token) <= 8:
            return "****"
        return f"{token[:4]}****{token[-4:]}"

    @classmethod
    def scrub_text(cls, text: str, token: str | None = None) -> str:
        """Remove any sensitive tokens or password occurrences from a string/url."""
        return scrub_tokens(text, token)

    @classmethod
    def get_authenticated_url(
        cls,
        repo_url: str,
        username: str | None = None,
        token: str | None = None,
        engine_root: Path | str | None = None,
    ) -> str:
        """
        Inject credentials into HTTPS repository URL for headless non-interactive git commands.
        e.g. https://github.com/owner/repo.git -> https://user:token@github.com/owner/repo.git
        If url is a local path, already authenticated, or SSH, returns as-is.
        """
        if not repo_url or not (repo_url.startswith("http://") or repo_url.startswith("https://")):
            return repo_url

        cfg = cls.load_config(engine_root)
        user = username or cfg.get("github_username") or ""
        tok = token or cfg.get("github_token") or ""

        if not tok:
            return repo_url

        # Check if already authenticated
        if "@" in repo_url.split("//", 1)[-1]:
            return repo_url

        parsed = urllib.parse.urlsplit(repo_url)
        auth_netloc = f"{user}:{tok}@{parsed.netloc}" if user else f"{tok}@{parsed.netloc}"
        auth_url = urllib.parse.urlunsplit((parsed.scheme, auth_netloc, parsed.path, parsed.query, parsed.fragment))
        return auth_url

    @classmethod
    def test_connection(
        cls,
        repo_url: str,
        username: str | None = None,
        token: str | None = None,
        engine_root: Path | str | None = None,
    ) -> dict[str, Any]:
        """
        Test remote repository connection using git ls-remote in headless non-interactive mode.
        """
        git_bin = shutil.which("git")
        if not git_bin:
            return {"success": False, "message": "Git executable not found in PATH."}

        if not repo_url or not repo_url.strip():
            return {"success": False, "message": "Repository URL is required to test connection."}

        clean_url = repo_url.strip()
        auth_url = cls.get_authenticated_url(clean_url, username=username, token=token, engine_root=engine_root)

        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"

        try:
            res = subprocess.run(
                [git_bin, "ls-remote", "--heads", auth_url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=12,
                check=False,
                creationflags=_NO_WINDOW_FLAG,
                shell=False,
            )
            clean_stdout = cls.scrub_text(res.stdout, token)
            clean_stderr = cls.scrub_text(res.stderr, token)

            if res.returncode == 0:
                branches = []
                for line in clean_stdout.splitlines():
                    parts = line.strip().split("\t")
                    if len(parts) == 2 and parts[1].startswith("refs/heads/"):
                        branches.append(parts[1].replace("refs/heads/", ""))
                return {
                    "success": True,
                    "message": "Connection successful! Remote repository verified.",
                    "branches": branches,
                }
            else:
                err_msg = clean_stderr.strip() or clean_stdout.strip() or "Failed to connect to remote repository."
                return {
                    "success": False,
                    "message": f"Connection failed: {err_msg}",
                    "branches": [],
                }
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "Connection timed out (12s). Check URL or network access."}
        except Exception as exc:
            return {"success": False, "message": f"Error testing connection: {cls.scrub_text(str(exc), token)}"}

    @classmethod
    def create_remote_repo(
        cls,
        repo_name: str,
        private: bool = False,
        username: str | None = None,
        token: str | None = None,
        engine_root: Path | str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new remote repository on GitHub via the GitHub REST API.
        If the repository already exists (HTTP 422), links cleanly to the existing repository.
        """
        cfg = cls.load_config(engine_root)
        user = username or cfg.get("github_username") or ""
        tok = token or cfg.get("github_token") or ""

        if not tok:
            return {
                "success": False,
                "message": "Personal Access Token (PAT) is required to auto-create GitHub repositories. Configure it in GitHub Settings.",
            }

        repo_name = repo_name.strip()
        if not repo_name:
            return {"success": False, "message": "Repository name cannot be empty."}

        url = "https://api.github.com/user/repos"
        payload = {
            "name": repo_name,
            "private": private,
            "description": description or f"Repository managed by SPD Analysis Engine ({repo_name})",
            "auto_init": False,
        }
        data_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {tok}",
                "User-Agent": "SPD-Analysis-Engine",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(resp_body) if resp_body else {}
                clone_url = data.get("clone_url") or (f"https://github.com/{user}/{repo_name}.git" if user else f"https://github.com/{repo_name}.git")
                html_url = data.get("html_url") or (f"https://github.com/{user}/{repo_name}" if user else f"https://github.com/{repo_name}")
                return {
                    "success": True,
                    "created": True,
                    "already_exists": False,
                    "message": f"Successfully created GitHub repository '{repo_name}'.",
                    "clone_url": clone_url,
                    "html_url": html_url,
                    "full_name": data.get("full_name") or (f"{user}/{repo_name}" if user else repo_name),
                }
        except urllib.error.HTTPError as http_err:
            raw_err_body = ""
            try:
                raw_err_body = http_err.read().decode("utf-8", errors="replace")
            except Exception:
                pass

            # Handle 422: Repository already exists on this account
            if http_err.code == 422:
                clone_url = f"https://github.com/{user}/{repo_name}.git" if user else f"https://github.com/{repo_name}.git"
                html_url = f"https://github.com/{user}/{repo_name}" if user else f"https://github.com/{repo_name}"
                return {
                    "success": True,
                    "created": False,
                    "already_exists": True,
                    "message": f"Repository '{repo_name}' already exists on GitHub account. Linked successfully.",
                    "clone_url": clone_url,
                    "html_url": html_url,
                    "full_name": f"{user}/{repo_name}" if user else repo_name,
                }
            elif http_err.code in (401, 403):
                msg = cls.scrub_text(raw_err_body, tok)
                return {
                    "success": False,
                    "message": f"GitHub authentication error ({http_err.code}): verify your Personal Access Token has 'repo' scope. {msg}".strip(),
                }
            else:
                msg = cls.scrub_text(raw_err_body or str(http_err), tok)
                return {
                    "success": False,
                    "message": f"GitHub API error ({http_err.code}): {msg}",
                }
        except Exception as exc:
            return {
                "success": False,
                "message": f"Failed to communicate with GitHub API: {cls.scrub_text(str(exc), tok)}",
            }


def push_to_remote(
    repo_path: Path | str,
    remote_url: str | None = None,
    commit_message: str | None = None,
    changelog_content: str | None = None,
    branch: str = "main",
    remote_name: str = "origin",
    engine_root: Path | str | None = None,
) -> dict[str, Any]:
    """Top-level convenience alias for ShadowGit.push_to_remote."""
    return ShadowGit.push_to_remote(
        repo_path,
        remote_url=remote_url,
        commit_message=commit_message,
        changelog_content=changelog_content,
        branch=branch,
        remote_name=remote_name,
        engine_root=engine_root,
    )


_ensure_shadow_repo = ShadowGit._ensure_shadow_repo
init_primary_repo = ShadowGit.init_primary_repo
```

---

### [8/27] File: `scripts\ide_profiles.py`
- **Lines:** 183 | **Size:** 6.05 KB | **Type:** py

```python
"""
scripts/ide_profiles.py
=======================
IDE Environment Profiles and Process Tree Whitelisting for the SPD Analysis Engine.

Enforces strict AI IDE process isolation by verifying process ancestry trees,
whitelisting supported AI environments (Antigravity, Cursor, Windsurf, Claude Code, Custom),
and explicitly rejecting native Microsoft VS Code instances unless they descend from an
authorized AI process tree.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import psutil

logger = logging.getLogger(__name__)

IDE_PROFILES: dict[str, dict[str, Any]] = {
    "antigravity": {
        "label": "Google Antigravity",
        "executables": ["antigravity.exe", "Antigravity.exe", "antigravity"],
        "process_markers": ["antigravity", "gemini", "agy"],
        "env_markers": ["ANTIGRAVITY", "GEMINI_CLI"],
    },
    "cursor": {
        "label": "Cursor",
        "executables": ["Cursor.exe", "cursor.exe", "cursor"],
        "process_markers": ["cursor", "anysphere"],
        "env_markers": ["CURSOR_TRACE_ID"],
    },
    "windsurf": {
        "label": "Windsurf",
        "executables": ["Windsurf.exe", "windsurf.exe", "windsurf"],
        "process_markers": ["windsurf", "codeium"],
        "env_markers": ["WINDSURF"],
    },
    "claude_code": {
        "label": "Claude Code",
        "executables": ["claude.exe", "claude", "node.exe", "node"],
        "process_markers": ["claude-code", "@anthropic-ai/claude-code"],
        "env_markers": ["CLAUDE_CODE"],
    },
    "custom": {
        "label": "Custom Process/Marker",
        "executables": [],
        "process_markers": [],
        "env_markers": [],
    },
}


def get_profile(profile_name: str, custom_marker: str | None = None) -> dict[str, Any]:
    """Retrieve an IDE profile dictionary, merging custom markers if provided."""
    key = (profile_name or "antigravity").lower().strip()
    profile = IDE_PROFILES.get(key)
    if not profile:
        profile = IDE_PROFILES["antigravity"]

    res = {
        "key": key,
        "label": profile["label"],
        "executables": list(profile["executables"]),
        "process_markers": list(profile["process_markers"]),
        "env_markers": list(profile["env_markers"]),
    }
    if custom_marker:
        cm = str(custom_marker).strip()
        if cm:
            res["process_markers"].append(cm.lower())
            if cm.lower().endswith(".exe") or "." not in cm:
                res["executables"].append(cm.lower())
    return res


def _is_native_vscode(name: str, exe: str, cmdline_str: str) -> bool:
    """Check if process matches native Microsoft VS Code (without AI branding)."""
    name_low = (name or "").lower()
    exe_low = (exe or "").lower()
    cmd_low = (cmdline_str or "").lower()

    if name_low in ("code.exe", "code"):
        return True
    if "microsoft vs code" in exe_low or "microsoft vs code" in cmd_low:
        return True
    return False


def is_process_whitelisted(
    proc: psutil.Process | None,
    profile_name: str = "antigravity",
    custom_marker: str | None = None,
) -> bool:
    """
    Verify whether *proc* or any of its ancestors in the process tree belongs to
    the whitelisted AI IDE profile.

    Explicitly suppresses native Microsoft VS Code unless it is running as a child
    process of an authorized AI environment (e.g. Antigravity IDE).
    """
    if proc is None:
        return False

    prof = get_profile(profile_name, custom_marker)
    executables = {e.lower() for e in prof["executables"]}
    markers = [m.lower() for m in prof["process_markers"]]
    env_markers = prof["env_markers"]

    # Retrieve process ancestry tree: [proc, parent, grandparent, ...]
    tree: list[psutil.Process] = [proc]
    try:
        tree.extend(proc.parents())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, Exception):
        pass

    ai_match_found = False
    native_vscode_in_tree = False
    ai_ancestor_of_vscode = False

    for idx, p in enumerate(tree):
        try:
            name = (p.name() or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, Exception):
            name = ""

        try:
            exe = (p.exe() or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, Exception):
            exe = ""

        try:
            cmd = " ".join(p.cmdline() or []).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, Exception):
            cmd = ""

        # Check if this node is native VS Code
        is_node_vscode = _is_native_vscode(name, exe, cmd)
        if is_node_vscode:
            native_vscode_in_tree = True

        # Check for profile match on this node
        matches_node = False
        if name in executables or Path(exe).name.lower() in executables:
            matches_node = True

        if not matches_node:
            for m in markers:
                if m in cmd or m in name or m in exe:
                    matches_node = True
                    break

        if not matches_node and env_markers:
            try:
                env = p.environ()
                for em in env_markers:
                    if em in env or em.lower() in env:
                        matches_node = True
                        break
            except Exception:
                pass

        if matches_node:
            # If the node matched solely because it's node.exe or similar, verify it has markers
            if name in ("node.exe", "node") and prof["key"] == "claude_code":
                if not any(m in cmd for m in markers):
                    matches_node = False

            if matches_node:
                ai_match_found = True
                # If native VS Code appeared earlier in the descendant chain, this AI match is an ancestor of it
                if native_vscode_in_tree and idx > 0:
                    ai_ancestor_of_vscode = True
                break

    # If native VS Code is in tree, it is ONLY whitelisted if an ancestor above it is an authorized AI environment
    if native_vscode_in_tree:
        return ai_ancestor_of_vscode

    return ai_match_found
```

---

### [9/27] File: `scripts\orchestrator.py`
- **Lines:** 1160 | **Size:** 44.29 KB | **Type:** py

```python
"""
scripts/orchestrator.py
=======================
Multi-worker process supervisor and cross-CLI-invocation process registry
for the SPD Analysis Engine.

Key fixes in this revision
--------------------------
Issue 1 – CLI State Persistence (PID File Reconciliation)
    Every ``Supervisor`` instance starts with an empty in-memory
    ``_registry``.  ``_discover_active_workers()`` is called at
    ``__init__`` **and** at the top of ``get_status()``,
    ``stop_project()``, and ``list_projects()``.  It scans
    ``current/<slug>/worker.pid``, verifies the PID via psutil, and
    re-populates ``_registry`` so that ``stop`` and ``status`` work
    seamlessly across separate CLI invocations.

Issue 2 – Worker Process Detachment & Silent Logging
    Workers are now spawned as **fully detached** processes:

    * Windows: ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP``
    * POSIX:   ``start_new_session=True``

    stdout and stderr are redirected to ``current/<slug>/worker.log``
    (append mode) so heartbeat JSON and log messages go to a file rather
    than the user's terminal.  The stdout-pipe reader thread is removed.
    Worker status (uptime, session_id, …) is now read from
    ``current/<slug>/worker.status.json`` which the worker updates on
    each heartbeat.

_WorkerEntry modes
------------------
Spawned (``proc is not None``)
    Created in this process via ``subprocess.Popen``.  ``proc.poll()``,
    ``proc.wait()``, ``proc.terminate()``, ``proc.kill()`` are available.

Reconciled (``proc is None``)
    Discovered from a ``worker.pid`` file written by an earlier CLI
    invocation.  Only ``psutil`` (or ``os.kill(pid, 0)``) is used for
    liveness; signals are sent via ``os.kill()`` directly.

Thread safety
-------------
``_registry`` is mutated only from the main thread.
``_discover_active_workers()`` is not thread-safe by design; callers
that embed ``Supervisor`` in an async framework should add external
locking around ``start_project`` / ``stop_project``.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "psutil not installed — memory/CPU stats and PID verification "
        "will be limited.  Run: pip install psutil"
    )

from .storage_rotator import StorageRotator, _slug, update_project_meta  # noqa: WPS436

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Seconds to wait for worker to exit gracefully before force-killing.
_STOP_TIMEOUT_S: float = 10.0

#: Seconds to wait for ``worker.pid`` to appear after spawning.
_BOOT_TIMEOUT_S: float = 8.0

#: How long the reaper daemon waits between liveness checks.
_REAPER_INTERVAL_S: float = 5.0


# ---------------------------------------------------------------------------
# _WorkerEntry
# ---------------------------------------------------------------------------

class _WorkerEntry:
    """
    Runtime record for a single project worker.

    Supports two creation modes:

    **Spawned** (``proc`` is not ``None``)
        This Supervisor launched the worker in the current process via
        ``subprocess.Popen``.  All ``Popen`` methods are available.

    **Reconciled** (``proc`` is ``None``)
        The worker was discovered from a ``worker.pid`` file on disk.
        Liveness is checked via ``psutil`` or ``os.kill(pid, 0)``.
        Signals are sent via ``os.kill(pid, …)``.

    Parameters
    ----------
    name          : Project name as supplied by the caller.
    target_path   : Absolute path being monitored.
    pid           : OS process ID of the worker.
    proc          : ``Popen`` handle (spawned mode only).
    log_path      : Path to ``worker.log`` for this project.
    started_at_iso: UTC timestamp string from ``worker.pid``.
    """

    def __init__(
        self,
        name: str,
        target_path: str,
        pid: int,
        *,
        proc: subprocess.Popen | None = None,
        log_path: Path | None = None,
        started_at_iso: str | None = None,
    ) -> None:
        self.name = name
        self.target_path = target_path
        self.pid = pid
        self.proc = proc
        self.log_path = log_path
        self.started_at_iso = started_at_iso
        self._mono_start = time.monotonic()
        self.session_id: int | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def reconciled(self) -> bool:
        """``True`` if this entry was discovered from disk (not spawned here)."""
        return self.proc is None

    @property
    def exit_code(self) -> int | None:
        """Exit code — available only for spawned workers after they exit."""
        return self.proc.returncode if self.proc is not None else None

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        """Return ``True`` if the worker OS process is still running."""
        if self.proc is not None:
            return self.proc.poll() is None

        # Reconciled path — prefer psutil, fall back to os.kill signal-0
        if _PSUTIL_AVAILABLE:
            try:
                p = psutil.Process(self.pid)
                return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                return False

        try:
            os.kill(self.pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def uptime_s(self) -> float:
        """
        Best-effort uptime in seconds.

        Uses ``psutil.Process.create_time()`` (wall-clock accurate) when
        psutil is available; otherwise falls back to a monotonic timer
        started when this entry was created.
        """
        if _PSUTIL_AVAILABLE:
            try:
                return time.time() - psutil.Process(self.pid).create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return time.monotonic() - self._mono_start


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

class Supervisor:
    """
    Multi-worker process supervisor for the SPD Analysis Engine.

    State is persisted across CLI invocations via ``worker.pid`` files in
    ``current/<slug>/``.  Every public method calls
    ``_discover_active_workers()`` first so that separate ``python start.py``
    invocations share a consistent view of running workers.

    Parameters
    ----------
    engine_root : Path | str | None
        Root of the ``spd_analysis_engine/`` installation.
        Defaults to the parent of the ``scripts/`` directory.

    Examples
    --------
    Programmatic (long-lived process) usage::

        from scripts.orchestrator import Supervisor

        sup = Supervisor("/path/to/spd_analysis_engine")
        sup.start_project("alpha", "/workspace/alpha", scaffold=True)
        time.sleep(5)
        print(sup.get_status("alpha"))
        sup.stop_project("alpha")
    """

    def __init__(self, engine_root: Path | str | None = None) -> None:
        if engine_root is None:
            engine_root = Path(__file__).resolve().parent.parent
        self._root = Path(engine_root).resolve()
        self._rotator = StorageRotator(self._root)

        # Registry keyed by project *name* (not slug)
        self._registry: dict[str, _WorkerEntry] = {}

        self._worker_script = Path(__file__).resolve().parent / "project_worker.py"

        # Background reaper daemon
        self._reaper_stop = threading.Event()
        self._reaper = threading.Thread(
            target=self._reaper_loop,
            name="spd-reaper",
            daemon=True,
        )
        self._reaper.start()

        # Immediately reconcile with any workers left running from a
        # previous CLI invocation.
        self._discover_active_workers()

        logger.info(
            "Supervisor ready | root=%s | tracked_workers=%d",
            self._root, len(self._registry),
        )

    # ==================================================================
    # PID-file reconciliation  (Issue 1 fix)
    # ==================================================================

    def _discover_active_workers(self) -> None:
        """
        Scan ``current/`` for live ``worker.pid`` files and update ``_registry``.

        This is the heart of the cross-CLI-invocation fix.  It is called:

        * At ``__init__`` — so every CLI session starts with a full picture.
        * At the top of ``get_status()``, ``stop_project()``, and
          ``list_projects()`` — so stale or newly-started workers are always
          reflected.

        Algorithm
        ---------
        For every ``current/<slug>/worker.pid`` found on disk:

        1. If already tracked in ``_registry`` with the same PID and alive
           → skip (nothing to do).
        2. Parse PID, project name, and metadata from the JSON file.
        3. Call ``_verify_worker_pid()`` to confirm the PID belongs to a live
           ``project_worker.py`` process.
        4. Alive  → create a *reconciled* ``_WorkerEntry`` and register it.
        5. Stale  → rename ``worker.pid`` → ``worker.pid.stale`` and skip.
        """
        current_dir = self._root / "current"
        if not current_dir.exists():
            return

        for proj_dir in sorted(current_dir.iterdir()):
            if not proj_dir.is_dir():
                continue

            slug = proj_dir.name
            pid_file = proj_dir / "worker.pid"

            if not pid_file.exists():
                continue

            # ---- Parse the PID file ----
            try:
                data: dict = json.loads(pid_file.read_text(encoding="utf-8"))
                pid = int(data["pid"])
                project_name = str(data.get("project", slug))
                target_path = str(data.get("target_path", ""))
                started_at_iso = str(data.get("started_at", ""))
            except Exception as exc:
                logger.warning("Cannot parse worker.pid in '%s': %s", proj_dir, exc)
                continue

            # ---- Check existing registry entry ----
            existing = self._registry.get(project_name)
            if existing is not None:
                if existing.pid == pid and existing.is_alive():
                    # Already correctly tracked
                    continue
                # Entry is stale (different PID or dead) — evict and re-check
                logger.debug(
                    "Evicting stale registry entry for '%s' "
                    "(was PID %s, disk says PID %s)",
                    project_name, existing.pid, pid,
                )
                del self._registry[project_name]

            # ---- Verify the PID is a live project_worker process ----
            alive = self._verify_worker_pid(pid, project_name)
            if alive is False:
                # Confirmed stale — archive the PID file and move on
                logger.info(
                    "Stale PID file for '%s' (PID %s is dead or wrong process)",
                    project_name, pid,
                )
                self._mark_stale_pid(pid_file, slug)
                continue
            # alive is True or None (psutil unavailable → assume alive)

            # ---- Create a reconciled entry ----
            entry = _WorkerEntry(
                name=project_name,
                target_path=target_path,
                pid=pid,
                log_path=proj_dir / "worker.log",
                started_at_iso=started_at_iso,
            )

            # Populate session_id from status file if it already exists
            status_data = self._read_status_file(proj_dir)
            if status_data.get("session_id") is not None:
                entry.session_id = int(status_data["session_id"])

            self._registry[project_name] = entry
            logger.info(
                "Reconciled worker '%s' (PID %s, started %s)",
                project_name, pid, started_at_iso or "unknown",
            )

    def _verify_worker_pid(self, pid: int, name: str) -> bool | None:
        """
        Confirm a PID belongs to a live ``project_worker.py`` process.

        Returns
        -------
        ``True``
            PID is alive and its cmdline contains ``project_worker``.
        ``False``
            PID does not exist, is a zombie, or belongs to a different process
            (stale PID file from a crash).
        ``None``
            Cannot determine (psutil unavailable or ``AccessDenied``);
            caller should assume alive to avoid false-positive stops.
        """
        if not _PSUTIL_AVAILABLE:
            return None

        try:
            p = psutil.Process(pid)
            if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                return False
            cmdline = p.cmdline()
            if any("project_worker" in arg for arg in cmdline):
                return True
            logger.warning(
                "PID %s claimed by '%s' is not project_worker "
                "(cmdline: %s) — treating as stale",
                pid, name, " ".join(cmdline[:6]),
            )
            return False
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            # Can't inspect; assume valid rather than accidentally stopping
            logger.debug(
                "AccessDenied checking PID %s for '%s' — assuming valid", pid, name
            )
            return None

    @staticmethod
    def _mark_stale_pid(pid_file: Path, slug: str) -> None:
        """
        Rename ``worker.pid`` → ``worker.pid.stale`` for post-mortem inspection.

        The stale file is preserved (not deleted) so engineers can review
        what PID was left behind after a crash.
        """
        stale = pid_file.with_name("worker.pid.stale")
        try:
            pid_file.rename(stale)
            logger.info("Stale PID file preserved as worker.pid.stale for slug '%s'", slug)
        except OSError as exc:
            logger.warning("Could not rename stale PID file %s: %s", pid_file, exc)

    # ==================================================================
    # Status-file helpers
    # ==================================================================

    @staticmethod
    def _read_status_file(proj_dir: Path) -> dict[str, Any]:
        """
        Read and parse ``worker.status.json``; return ``{}`` on any error.

        The status file is written atomically by the worker on every
        heartbeat cycle and contains ``pid``, ``session_id``, ``uptime_s``,
        and ``ts`` among other fields.
        """
        path = proj_dir / "worker.status.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Could not read status file %s: %s", path, exc)
            return {}

    # ==================================================================
    # Public API
    # ==================================================================

    def start_project(
        self,
        name: str,
        path: str,
        scaffold: bool = True,
        debounce: float = 3.5,
        track_reads: bool = True,
        track_exec: bool = True,
        shadow_git: bool = True,
        git_init_primary: bool = False,
        ide_profile: str = "antigravity",
        ide_custom_marker: str | None = None,
    ) -> dict[str, Any]:
        """
        Spawn a **detached** worker subprocess for *name*.

        The worker runs completely independently of this CLI process:

        * **Windows**: ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` —
          no console window; own process group for ``CTRL_BREAK_EVENT``.
        * **POSIX**: ``start_new_session=True`` — own session, no TTY.

        Both stdout and stderr are redirected to
        ``current/<slug>/worker.log`` (append mode).  No pipe is opened
        so the worker's output never appears in the user's terminal.

        After spawning, this method waits up to ``_BOOT_TIMEOUT_S`` seconds
        for ``worker.pid`` to appear on disk, confirming the worker has
        bootstrapped successfully.

        Parameters
        ----------
        name              : Unique project name.
        path              : Absolute path to the directory to monitor.
        scaffold          : Create *path* automatically if it does not exist.
        debounce          : Sliding debounce quiet period (default: 3.5).
        track_reads       : Track file read/analysis handles via psutil (default: True).
        track_exec        : Track terminal execution commands (default: True).
        shadow_git        : Maintain shadow git micro-versioning (default: True).
        git_init_primary  : Initialize primary git repo in target directory if missing (default: False).
        ide_profile       : Monitored AI environment profile (default: 'antigravity').
        ide_custom_marker : Custom process marker or executable name when using custom profile.

        Returns
        -------
        dict
            ``{ status, pid, project, target_path, log_path }``

        Raises
        ------
        ValueError
            If a live worker for *name* is already registered.
        FileNotFoundError
            If ``project_worker.py`` cannot be located.
        """
        # Always reconcile before duplicate check — picks up workers from
        # earlier CLI invocations.
        self._discover_active_workers()

        if name in self._registry and self._registry[name].is_alive():
            entry = self._registry[name]
            raise ValueError(
                f"A worker for project '{name}' is already running "
                f"(PID {entry.pid}).  "
                f"Run 'python start.py stop --project {name}' first."
            )

        if not self._worker_script.exists():
            raise FileNotFoundError(
                f"Worker script not found: {self._worker_script}"
            )

        # ----------------------------------------------------------
        # Prepare directories and log file
        # ----------------------------------------------------------
        slug = _slug(name)
        proj_dir = self._root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)  # StorageRotator re-creates this
        log_path = proj_dir / "worker.log"

        update_project_meta(
            self._root,
            slug,
            target_path=str(Path(path).resolve()),
            ide_profile=ide_profile,
            ide_custom_marker=ide_custom_marker or "",
        )

        # Write a visible delimiter into the log so sessions are easy to spot
        with open(log_path, "a", encoding="utf-8", buffering=1) as lf:
            lf.write(
                f"\n{'=' * 72}\n"
                f"  SPD Worker Session Start\n"
                f"  Project : {name}\n"
                f"  Target  : {path}\n"
                f"  Started : {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
                f"{'=' * 72}\n\n"
            )

        # ----------------------------------------------------------
        # Build command
        # ----------------------------------------------------------
        py_exe = sys.executable
        if sys.platform == "win32":
            cand = Path(sys.executable).parent / "pythonw.exe"
            if cand.exists():
                py_exe = str(cand)

        cmd = [
            py_exe,
            str(self._worker_script),
            "--name", name,
            "--path", str(Path(path).resolve()),
            "--engine-root", str(self._root),
        ]
        if scaffold:
            cmd.append("--scaffold")
        if debounce != 3.5:
            cmd.extend(["--debounce", str(debounce)])
        if not track_reads:
            cmd.append("--no-track-reads")
        if not track_exec:
            cmd.append("--no-track-exec")
        if not shadow_git:
            cmd.append("--no-shadow-git")
        if git_init_primary:
            cmd.append("--git-init-primary")
        if ide_profile:
            cmd.extend(["--ide-profile", ide_profile])
        if ide_custom_marker:
            cmd.extend(["--ide-custom-marker", ide_custom_marker])

        logger.info("Spawning worker | cmd=%s | log=%s", " ".join(cmd), log_path)

        # ----------------------------------------------------------
        # Spawn detached  (Issue 2 fix)
        # ----------------------------------------------------------
        log_fd = open(log_path, "a", encoding="utf-8", buffering=1)
        proc = None
        spawned_pid: int | None = None
        try:
            if sys.platform == "win32":
                # Close log file handle in parent since worker redirects stdout/stderr internally
                log_fd.close()

                cmd_args = [
                    f'"{py_exe}"',
                    f'"{self._worker_script}"',
                    "--name", f'"{name}"',
                    "--path", f'"{Path(path).resolve()}"',
                    "--engine-root", f'"{self._root}"',
                ]
                if scaffold:
                    cmd_args.append("--scaffold")
                if debounce != 3.5:
                    cmd_args.extend(["--debounce", str(debounce)])
                if not track_reads:
                    cmd_args.append("--no-track-reads")
                if not track_exec:
                    cmd_args.append("--no-track-exec")
                if not shadow_git:
                    cmd_args.append("--no-shadow-git")
                if git_init_primary:
                    cmd_args.append("--git-init-primary")
                if ide_profile:
                    cmd_args.extend(["--ide-profile", f'"{ide_profile}"'])
                if ide_custom_marker:
                    cmd_args.extend(["--ide-custom-marker", f'"{ide_custom_marker}"'])
                cmd_line = " ".join(cmd_args)

                spawned_ok = False
                no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                # 1. Primary approach: WMI Win32_Process.Create via PowerShell Invoke-CimMethod.
                # Running via WmiPrvSE.exe detaches the process completely from the caller's Job Object
                # and console session, guaranteeing true daemon persistence on Windows.
                try:
                    ps_script = (
                        f"$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
                        f"-Arguments @{{CommandLine = '{cmd_line}'}}; "
                        f"if ($res.ReturnValue -eq 0) {{ Write-Host $res.ProcessId }}"
                    )
                    res = subprocess.run(
                        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5.0,
                        creationflags=no_window,
                        shell=False,
                    )
                    out = res.stdout.strip()
                    if out.isdigit():
                        spawned_ok = True
                        spawned_pid = int(out)
                        logger.info("Worker process spawned via WMI (PID %s)", spawned_pid)
                except Exception as exc:
                    logger.debug("WMI spawn failed (%s), attempting powershell Start-Process fallback", exc)

                # 2. Fallback: powershell Start-Process
                if not spawned_ok:
                    try:
                        arg_list = f'"{self._worker_script}" --name "{name}" --path "{Path(path).resolve()}" --engine-root "{self._root}"'
                        if scaffold:
                            arg_list += " --scaffold"
                        if debounce != 3.5:
                            arg_list += f" --debounce {debounce}"
                        if not track_reads:
                            arg_list += " --no-track-reads"
                        if not track_exec:
                            arg_list += " --no-track-exec"
                        if not shadow_git:
                            arg_list += " --no-shadow-git"
                        if git_init_primary:
                            arg_list += " --git-init-primary"
                        ps_cmd = [
                            "powershell", "-NoProfile", "-NonInteractive", "-Command",
                            f"Start-Process '{py_exe}' -ArgumentList '{arg_list}' -WindowStyle Hidden"
                        ]
                        subprocess.run(ps_cmd, check=True, timeout=5.0, creationflags=no_window, shell=False)
                        spawned_ok = True
                    except Exception as exc:
                        logger.debug("Start-Process failed (%s), attempting ShellExecuteW fallback", exc)

                # 3. Fallback: ShellExecuteW
                if not spawned_ok:
                    try:
                        import ctypes
                        SW_HIDE = 0
                        params = f'"{self._worker_script}" --name "{name}" --path "{Path(path).resolve()}" --engine-root "{self._root}"'
                        if scaffold:
                            params += " --scaffold"
                        if debounce != 3.5:
                            params += f" --debounce {debounce}"
                        if not track_reads:
                            params += " --no-track-reads"
                        if not track_exec:
                            params += " --no-track-exec"
                        if not shadow_git:
                            params += " --no-shadow-git"
                        if git_init_primary:
                            params += " --git-init-primary"
                        ret = ctypes.windll.shell32.ShellExecuteW(
                            None, "open", py_exe, params, str(self._root), SW_HIDE
                        )
                        if ret > 32:
                            spawned_ok = True
                        else:
                            raise RuntimeError(f"ShellExecuteW returned code {ret}")
                    except Exception as exc:
                        logger.error("All Windows detached spawn methods failed: %s", exc)
                        raise RuntimeError(f"Could not spawn worker process on Windows: {exc}") from exc

                proc = None
            else:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_fd,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,     # POSIX: new session, detached from TTY
                    close_fds=True,
                )
                log_fd.close()
        finally:
            try:
                log_fd.close()
            except Exception:
                pass

        # ----------------------------------------------------------
        # Poll for worker.pid for up to 3.0 seconds
        # ----------------------------------------------------------
        pid_file = proj_dir / "worker.pid"
        deadline = time.monotonic() + 3.0
        real_pid = spawned_pid or (proc.pid if proc is not None else 0)

        while time.monotonic() < deadline:
            if pid_file.exists():
                try:
                    pid_data = json.loads(pid_file.read_text(encoding="utf-8"))
                    real_pid = int(pid_data["pid"])
                    break
                except (json.JSONDecodeError, KeyError, ValueError, OSError):
                    pass  # File might be partially written, retry
            if proc is not None and proc.poll() is not None:
                logger.error(
                    "Worker '%s' exited with code %s before writing worker.pid  "
                    "-- check log: %s",
                    name, proc.returncode, log_path,
                )
                break
            time.sleep(0.1)

        if not pid_file.exists() and real_pid == 0:
            logger.warning(
                "worker.pid not found within 3.0s for '%s'  "
                "— worker may have failed (log: %s)",
                name, log_path,
            )

        entry = _WorkerEntry(
            name=name,
            target_path=path,
            pid=real_pid,
            proc=proc,
            log_path=log_path,
        )
        self._registry[name] = entry

        logger.info("Worker started | project=%s | PID=%s", name, real_pid)

        # Try to pre-populate session_id from status file (may not exist yet)
        status_data = self._read_status_file(proj_dir)
        if status_data.get("session_id") is not None:
            entry.session_id = int(status_data["session_id"])

        return {
            "status":      "started",
            "pid":         real_pid,
            "project":     name,
            "target_path": path,
            "log_path":    str(log_path),
        }

    def stop_project(self, name: str) -> dict[str, Any]:
        """
        Gracefully terminate the worker for *name* and archive its session.

        Works for both **spawned** workers (same CLI invocation) and
        **reconciled** workers discovered from ``worker.pid`` on disk.

        Stop sequence
        -------------
        1. Reconcile registry from disk (``_discover_active_workers``).
        2. Send ``SIGTERM`` / ``CTRL_BREAK_EVENT`` and wait up to
           ``_STOP_TIMEOUT_S`` seconds.
        3. Force-kill if still alive.
        4. Remove leftover ``worker.pid`` (the worker removes it on clean
           exit, but we clean up after a crash / force-kill).
        5. Invoke ``StorageRotator.archive_project()`` →
           ``current/`` → ``last_run/`` → ``history/``.

        Parameters
        ----------
        name : Project name passed to :meth:`start_project`.

        Returns
        -------
        dict
            ``{ status, project, pid, exit_code }``

        Raises
        ------
        KeyError
            If no live worker for *name* is found on disk or in memory.
        """
        self._discover_active_workers()

        if name not in self._registry:
            # Fallback: inspect disk directly in case registry missed it
            slug = _slug(name)
            pid_file = self._root / "current" / slug / "worker.pid"
            if pid_file.exists():
                try:
                    data = json.loads(pid_file.read_text(encoding="utf-8"))
                    pid = int(data["pid"])
                    self._registry[name] = _WorkerEntry(
                        name=name,
                        target_path=str(data.get("target_path", "")),
                        pid=pid,
                        log_path=self._root / "current" / slug / "worker.log",
                    )
                except Exception:
                    pass

        if name not in self._registry:
            raise KeyError(
                f"No running worker found for project '{name}'.  "
                f"Run 'python start.py list' to see known projects."
            )

        entry = self._registry[name]
        slug = _slug(name)
        pid = entry.pid
        exit_code: int | None = None

        if entry.is_alive():
            logger.info("Sending stop signal to worker '%s' (PID %s)", name, pid)
            if _PSUTIL_AVAILABLE and pid:
                try:
                    p = psutil.Process(pid)
                    if p.is_running():
                        p.terminate()
                        try:
                            p.wait(timeout=_STOP_TIMEOUT_S)
                        except psutil.TimeoutExpired:
                            logger.warning(
                                "Worker '%s' (PID %s) unresponsive after %.1fs — force-killing",
                                name, pid, _STOP_TIMEOUT_S,
                            )
                            p.kill()
                            p.wait(timeout=3.0)
                except psutil.NoSuchProcess:
                    pass
                except Exception as exc:
                    logger.warning("Error stopping process %s: %s", pid, exc)
            elif entry.proc is not None:
                self._send_stop(entry.proc)
                try:
                    entry.proc.wait(timeout=_STOP_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    entry.proc.kill()
                    entry.proc.wait()
                exit_code = entry.proc.returncode
            elif pid:
                self._send_stop_pid(pid)
                self._wait_for_pid_exit(pid, _STOP_TIMEOUT_S)
        else:
            logger.warning(
                "Worker '%s' (PID %s) already exited", name, pid
            )

        # Brief pause to ensure OS releases any file handles
        time.sleep(0.2)

        # ---- Clean up pid file (the worker may not have removed it) ----
        pid_file = self._root / "current" / slug / "worker.pid"
        if pid_file.exists():
            try:
                pid_file.unlink()
                logger.debug("Removed leftover worker.pid for '%s'", name)
            except OSError as exc:
                logger.debug("Could not remove worker.pid: %s", exc)

        # ---- Clean up status file if present ----
        status_file = self._root / "current" / slug / "worker.status.json"
        if status_file.exists():
            try:
                status_file.unlink()
            except OSError:
                pass

        # ---- Archive: current/ → last_run/ → history/ ----
        try:
            self._rotator.archive_project(name)
            logger.info("Session archived for project '%s'", name)
        except Exception as exc:  # noqa: BLE001
            logger.error("StorageRotator.archive_project failed for '%s': %s", name, exc)
            raise

        del self._registry[name]

        return {
            "status":    "stopped",
            "project":   name,
            "pid":       pid,
            "exit_code": exit_code or 0,
        }

    def get_status(self, name: str | None = None) -> list[dict[str, Any]]:
        """
        Return live status for one or all known workers.

        Reconciles with disk first so that workers started in previous CLI
        sessions are always visible.

        Status is augmented from two sources:

        * ``worker.status.json`` — session_id, heartbeat timestamp written
          by the worker every heartbeat interval.
        * ``psutil`` — RSS memory and CPU% sampled live.

        Parameters
        ----------
        name : Project name.  ``None`` queries all registered workers.

        Returns
        -------
        list[dict]
            Each dict contains:

            ``project``, ``pid``, ``target_path``, ``uptime_s``,
            ``alive``, ``exit_code``, ``session_id``,
            ``last_heartbeat_s`` (seconds since last status-file update,
            ``None`` if no status file exists yet),
            ``reconciled`` (``True`` if discovered from disk),
            ``log_path``, ``memory_mb``, ``cpu_percent``.
        """
        self._discover_active_workers()

        if name is not None:
            if name not in self._registry:
                return []
            targets = [self._registry[name]]
        else:
            targets = list(self._registry.values())

        results: list[dict[str, Any]] = []

        for entry in targets:
            proj_dir = self._root / "current" / _slug(entry.name)
            status_data = self._read_status_file(proj_dir)

            # Refresh session_id from latest status file
            with entry._lock:
                if status_data.get("session_id") is not None:
                    entry.session_id = int(status_data["session_id"])

            # Compute heartbeat age from the ISO timestamp in status file
            last_hb_s: float | None = None
            if "ts" in status_data:
                try:
                    ts = datetime.strptime(status_data["ts"], "%Y-%m-%dT%H:%M:%SZ")
                    ts = ts.replace(tzinfo=timezone.utc)
                    last_hb_s = round(
                        (datetime.now(timezone.utc) - ts).total_seconds(), 2
                    )
                except Exception:
                    pass

            row: dict[str, Any] = {
                "project":          entry.name,
                "pid":              entry.pid,
                "target_path":      entry.target_path,
                "uptime_s":         round(entry.uptime_s(), 2),
                "alive":            entry.is_alive(),
                "exit_code":        entry.exit_code,
                "session_id":       entry.session_id,
                "last_heartbeat_s": last_hb_s,
                "reconciled":       entry.reconciled,
                "log_path":         str(entry.log_path) if entry.log_path else None,
                "memory_mb":        None,
                "cpu_percent":      None,
                "powers":           status_data.get("powers", {}),
                "debounce":         status_data.get("debounce", {"active": False}),
                "recent_actions":   status_data.get("recent_actions", []),
            }

            if _PSUTIL_AVAILABLE and entry.is_alive():
                try:
                    ps = psutil.Process(entry.pid)
                    row["memory_mb"] = round(ps.memory_info().rss / 1_048_576, 2)
                    row["cpu_percent"] = ps.cpu_percent(interval=0.1)
                except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                    logger.debug("psutil error for PID %s: %s", entry.pid, exc)

            results.append(row)

        return results

    def list_projects(self) -> dict[str, Any]:
        """
        Return a combined view of projects across all storage tiers.

        Reconciles with disk first so running workers detected via
        ``worker.pid`` are included in the ``running`` section.

        Returns
        -------
        dict
            ``{ "running": { name: pid, … },
                "on_disk":  { slug: { current, last_run, history_runs }, … } }``
        """
        self._discover_active_workers()

        running = {
            entry.name: entry.pid
            for entry in self._registry.values()
            if entry.is_alive()
        }
        on_disk = self._rotator.list_all()
        return {"running": running, "on_disk": on_disk}

    def stop_all(self) -> list[dict[str, Any]]:
        """
        Stop every tracked worker.

        Returns
        -------
        list[dict]
            One result dict per worker (from :meth:`stop_project`).
        """
        self._discover_active_workers()
        names = list(self._registry.keys())
        results: list[dict[str, Any]] = []
        for name in names:
            try:
                results.append(self.stop_project(name))
            except Exception as exc:  # noqa: BLE001
                logger.error("Error stopping project '%s': %s", name, exc)
                results.append({"status": "error", "project": name, "error": str(exc)})
        return results

    # ==================================================================
    # Signal helpers
    # ==================================================================

    @staticmethod
    def _send_stop(proc: subprocess.Popen) -> None:
        """
        Send a graceful termination signal to a ``Popen`` object.

        Windows: ``proc.terminate()`` calls ``TerminateProcess()`` directly.
        POSIX:   ``SIGTERM``.

        Note: We do **not** use ``CTRL_BREAK_EVENT`` here because the worker
        is spawned without ``CREATE_NEW_PROCESS_GROUP``, so sending
        ``CTRL_BREAK_EVENT`` would signal the wrong process group.
        """
        proc.terminate()  # TerminateProcess on Windows; SIGTERM on POSIX

    @staticmethod
    def _send_stop_pid(pid: int) -> None:
        """
        Send a graceful termination signal to a raw PID (reconciled workers).

        Windows: ``psutil.Process(pid).terminate()`` → ``TerminateProcess()``.
        POSIX:   ``SIGTERM``.
        """
        try:
            if _PSUTIL_AVAILABLE:
                psutil.Process(pid).terminate()
            else:
                if sys.platform != "win32":
                    os.kill(pid, signal.SIGTERM)
                else:
                    # Fallback: use os.kill with SIGTERM (maps to TerminateProcess on Windows)
                    os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError) as exc:
            logger.debug("Could not send stop signal to PID %s: %s", pid, exc)

    @staticmethod
    def _kill_pid(pid: int) -> None:
        """Force-kill a PID when graceful stop times out."""
        try:
            if _PSUTIL_AVAILABLE:
                psutil.Process(pid).kill()
            elif sys.platform == "win32":
                # os.kill on Windows maps most signals to TerminateProcess
                os.kill(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGKILL)  # type: ignore[attr-defined]
        except (OSError, ProcessLookupError) as exc:
            logger.debug("Could not kill PID %s: %s", pid, exc)

    @staticmethod
    def _wait_for_pid_exit(pid: int, timeout: float) -> bool:
        """
        Poll until *pid* dies or *timeout* seconds elapse.

        Returns ``True`` if the process exited within the timeout, ``False``
        if it is still alive.
        """
        if _PSUTIL_AVAILABLE:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    psutil.Process(pid).wait(timeout=min(0.5, remaining))
                    return True
                except psutil.NoSuchProcess:
                    return True
                except psutil.TimeoutExpired:
                    continue
            return not psutil.pid_exists(pid)

        # Fallback without psutil
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)          # signal 0 = existence check
                time.sleep(0.2)
            except (OSError, ProcessLookupError):
                return True
        return False

    # ==================================================================
    # Background reaper
    # ==================================================================

    def _reaper_loop(self) -> None:
        """
        Daemon thread: detect and clean up workers that have died without
        going through :meth:`stop_project`.

        Runs every ``_REAPER_INTERVAL_S`` seconds.  For each dead worker:

        * Calls ``StorageRotator.archive_project()`` so the session is
          moved from ``current/`` to ``last_run/``.
        * Removes the entry from ``_registry``.

        Alive workers are not disturbed; heartbeat timeout enforcement is
        deferred to Phase 2.
        """
        while not self._reaper_stop.is_set():
            self._reaper_stop.wait(timeout=_REAPER_INTERVAL_S)

            dead: list[str] = []
            for name, entry in list(self._registry.items()):
                if not entry.is_alive():
                    logger.warning(
                        "Worker '%s' (PID %s) exited unexpectedly "
                        "(exit_code=%s) — archiving",
                        name, entry.pid,
                        entry.exit_code if entry.exit_code is not None else "unknown",
                    )
                    dead.append(name)

            for name in dead:
                try:
                    self._rotator.archive_project(name)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error archiving dead project '%s': %s", name, exc)
                self._registry.pop(name, None)

    def shutdown(self) -> None:
        """
        Cleanly shut down the ``Supervisor``: stop all workers, stop reaper.

        Call this when the orchestrator process itself is exiting to avoid
        leaving orphaned worker subprocesses behind.
        """
        logger.info("Supervisor shutdown — stopping all workers")
        self.stop_all()
        self._reaper_stop.set()
        self._reaper.join(timeout=5.0)
        logger.info("Supervisor shutdown complete")
```

---

### [10/27] File: `scripts\project_worker.py`
- **Lines:** 893 | **Size:** 34.71 KB | **Type:** py

```python
"""
scripts/project_worker.py
=========================
Isolated worker process that monitors a single target project directory.

This module is designed to be launched as a **detached subprocess** by the
orchestrator (``Supervisor.start_project``), but it can also be invoked
directly from the command line for debugging:

    python project_worker.py \\
        --name "my_project" \\
        --path "C:/workspace/my_project" \\
        [--scaffold] \\
        [--engine-root ".."]

Changes from the original version
----------------------------------
Issue 2 fix — Silent / detached logging
    * The Supervisor redirects this process's stdout **and** stderr to
      ``current/<slug>/worker.log`` (via ``subprocess.DETACHED_PROCESS``
      on Windows or ``start_new_session=True`` on POSIX).  No output
      ever appears in the user's terminal.
    * ``logging.basicConfig`` targets ``sys.stderr`` (which is the log
      file after the redirect).  ``_emit_json`` writes to stdout (also
      the log file).

Issue 1 fix — Enriched PID / status files for reconciliation
    * ``worker.pid`` now includes ``target_path`` and ``engine_root`` so
      the Supervisor can fully reconstruct the worker's context from disk
      without any in-memory state.
    * ``worker.status.json`` is written atomically on every heartbeat and
      contains ``pid``, ``project``, ``session_id``, ``uptime_s``, and
      ``ts``.  The Supervisor reads this file instead of tailing a pipe.

Architecture
------------
1. **Bootstrap** – Resolve paths, scaffold target if needed, initialise
   ``current/<slug>/`` via ``StorageRotator``, open ``SessionDB``, write
   ``worker.pid``.
2. **Signal handling** – SIGINT / SIGTERM / SIGBREAK → ``_clean_shutdown``
   (flushes WAL, closes DB, removes PID file, exits 0).
3. **Heartbeat loop** – Every 2 s: emit JSON to stdout (→ log file) and
   write ``worker.status.json`` atomically.
4. **Watchdog stub** – ``FileEventHandler`` is ready for Phase 2.
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure the engine root is on sys.path when run as a subprocess.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent          # …/scripts/
_ENGINE_ROOT_DEFAULT = _HERE.parent              # …/spd_analysis_engine/
sys.path.insert(0, str(_ENGINE_ROOT_DEFAULT.parent))  # parent of spd_analysis_engine

from spd_analysis_engine.scripts.storage_rotator import SessionDB, StorageRotator  # noqa: E402
from spd_analysis_engine.scripts.diff_calc import DiffCalculator  # noqa: E402
from spd_analysis_engine.scripts.event_bundler import EventBundler  # noqa: E402
from spd_analysis_engine.scripts.watcher_fs import ProjectWatcher  # noqa: E402
from spd_analysis_engine.scripts.git_shadow import ShadowGit  # noqa: E402
from spd_analysis_engine.scripts.watcher_proc import ProcessInspector  # noqa: E402
from spd_analysis_engine.scripts.shell_interceptor import ExecutionTracker  # noqa: E402

# ---------------------------------------------------------------------------
# Logging — targets sys.stderr which the Supervisor redirects to worker.log
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WORKER] %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


_utcnow_iso = _now_ist_iso


# ---------------------------------------------------------------------------
# Module-level state (accessible by signal handlers and atexit)
# ---------------------------------------------------------------------------
_session_db: SessionDB | None = None
_session_id: int | None = None
_pid_file: Path | None = None
_lock_file: Path | None = None
_status_file: Path | None = None          # worker.status.json
_start_time: float = 0.0
_shutdown_requested: bool = False
_project_name: str = "<unknown>"
_target_path_str: str = ""
_engine_root_str: str = ""
_diff_calc: DiffCalculator | None = None
_event_bundler: EventBundler | None = None
_project_watcher: ProjectWatcher | None = None
_patches_dir: Path | None = None
_shadow_git: ShadowGit | None = None
_proc_inspector: ProcessInspector | None = None
_exec_tracker: ExecutionTracker | None = None
_powers: dict[str, bool] = {}


# ---------------------------------------------------------------------------
# PID file & Session lock
# ---------------------------------------------------------------------------

def _write_pid_file(pid_path: Path) -> None:
    """
    Write a JSON PID file containing enough metadata for the Supervisor to
    reconcile this worker from disk in a future CLI invocation.

    Fields
    ------
    pid          : OS process ID.
    project      : Project name (used as the registry key in Supervisor).
    target_path  : Absolute path being monitored — stored so Supervisor
                   can rebuild the ``_WorkerEntry.target_path`` field.
    engine_root  : Engine root — stored so Supervisor can locate the
                   correct ``current/<slug>/`` directory.
    started_at   : UTC ISO-8601 timestamp.
    """
    payload = {
        "pid":         os.getpid(),
        "project":     _project_name,
        "target_path": _target_path_str,
        "engine_root": _engine_root_str,
        "started_at":  _utcnow_iso(),
        "powers":      _powers,
    }
    # Write atomically: write to a temp file then rename so the Supervisor
    # never reads a half-written PID file.
    tmp = pid_path.with_name("worker.pid.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(pid_path)
    logger.info("PID file written: %s", pid_path)


def _remove_pid_file() -> None:
    """Remove the PID file on clean exit."""
    global _pid_file
    if _pid_file and _pid_file.exists():
        try:
            _pid_file.unlink()
            logger.debug("PID file removed: %s", _pid_file)
        except OSError as exc:
            logger.warning("Could not remove PID file: %s", exc)


def _write_lock_file(lock_path: Path) -> None:
    """Write sentinel session.lock containing active PID and start ISO timestamp."""
    payload = {
        "pid": os.getpid(),
        "start_time": _now_ist_iso(),
        "project": _project_name,
        "target_path": _target_path_str,
    }
    tmp = lock_path.with_suffix(".lock.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(lock_path)
    logger.info("Session lock sentinel written: %s", lock_path)


def _remove_lock_file() -> None:
    """Remove sentinel lock file on clean exit."""
    global _lock_file
    if _lock_file and _lock_file.exists():
        try:
            _lock_file.unlink()
            logger.debug("Session lock removed: %s", _lock_file)
        except OSError as exc:
            logger.warning("Could not remove session lock: %s", exc)


# ---------------------------------------------------------------------------
# Status file  (read by Supervisor.get_status() instead of a stdout pipe)
# ---------------------------------------------------------------------------

def _write_status_file() -> None:
    """
    Atomically update ``worker.status.json`` with the current heartbeat data.

    Written on every heartbeat iteration so the Supervisor can read live
    uptime, session_id, and the last-updated timestamp without a pipe.

    Atomic write strategy: write to ``worker.status.json.tmp`` then rename,
    so the Supervisor never reads a partially-written file.
    """
    if _status_file is None:
        return

    debounce_info = _event_bundler.get_debounce_status() if _event_bundler else {"active": False}
    recent_actions = _event_bundler.get_recent_actions(40) if _event_bundler else []

    payload = {
        "pid":       os.getpid(),
        "project":   _project_name,
        "target_path": _target_path_str,
        "session_id":  _session_id,
        "uptime_s":  round(time.monotonic() - _start_time, 2),
        "ts":        _utcnow_iso(),
        "powers":    _powers,
        "debounce":  debounce_info,
        "recent_actions": recent_actions,
    }
    tmp = _status_file.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(_status_file)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write status file: %s", exc)


def _remove_status_file() -> None:
    """Remove the status file on clean exit."""
    global _status_file
    if _status_file and _status_file.exists():
        try:
            _status_file.unlink()
            logger.debug("Status file removed: %s", _status_file)
        except OSError as exc:
            logger.warning("Could not remove status file: %s", exc)


# ---------------------------------------------------------------------------
# JSON heartbeat emitter
# ---------------------------------------------------------------------------

def _emit_json(event: str, extra: dict | None = None) -> None:
    """
    Write a single JSON line to stdout.

    Since the Supervisor redirects this process's stdout to ``worker.log``,
    these lines land in the log file (not the terminal).  They serve as a
    structured, grep-friendly record of worker lifecycle events.

    Parameters
    ----------
    event : Short label (``"heartbeat"``, ``"startup"``, ``"shutdown"``).
    extra : Optional key/value pairs merged into the payload.
    """
    payload: dict = {
        "event":    event,
        "pid":      os.getpid(),
        "project":  _project_name,
        "uptime_s": round(time.monotonic() - _start_time, 2),
        "ts":       _utcnow_iso(),
    }
    if extra:
        payload.update(extra)
    line = json.dumps(payload)
    try:
        print(line, flush=True)
    except (OSError, BrokenPipeError):
        # Stdout was closed (e.g. detached + log file handle closed externally).
        # Fall back to stderr (also the log file) so the event is not silently lost.
        try:
            print(line, file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Shutdown / signal handling
# ---------------------------------------------------------------------------

def _clean_shutdown(signum: int | None = None, frame: object = None) -> None:
    """
    Gracefully terminate the worker.

    Steps
    -----
    1. Guard against re-entrant calls (``_shutdown_requested``).
    2. Emit a ``"shutdown"`` JSON event to the log.
    3. Stop the file watcher and flush any pending event bundle.
    4. Close the active session in the SQLite DB (status = ``STOPPED``).
    5. Checkpoint + close the WAL.
    6. Remove ``worker.pid`` and ``worker.status.json``.
    7. ``sys.exit(0)``.
    """
    global _shutdown_requested, _session_db, _session_id
    global _project_watcher, _event_bundler, _proc_inspector, _exec_tracker

    if _shutdown_requested:
        return
    _shutdown_requested = True

    sig_name = signal.Signals(signum).name if signum else "ATEXIT"
    logger.info("Shutdown requested via %s — flushing state…", sig_name)
    _emit_json("shutdown", {"reason": sig_name})

    # Stop filesystem observer first
    if _project_watcher is not None:
        try:
            _project_watcher.stop()
        except Exception as exc:
            logger.debug("Error stopping project watcher: %s", exc)
        _project_watcher = None

    # Stop process inspector
    if _proc_inspector is not None:
        try:
            _proc_inspector.stop()
        except Exception as exc:
            logger.debug("Error stopping ProcessInspector: %s", exc)
        _proc_inspector = None

    # Stop execution tracker
    if _exec_tracker is not None:
        try:
            _exec_tracker.stop()
        except Exception as exc:
            logger.debug("Error stopping ExecutionTracker: %s", exc)
        _exec_tracker = None

    # Flush any in-flight debounce window bundle
    if _event_bundler is not None:
        try:
            _event_bundler.flush()
            _event_bundler.stop()
        except Exception as exc:
            logger.debug("Error stopping event bundler: %s", exc)
        _event_bundler = None

    if _session_db is not None and _session_id is not None:
        try:
            _session_db.close_session(_session_id, status="STOPPED")
        except Exception as exc:  # noqa: BLE001
            logger.error("Error closing session row: %s", exc)

    if _session_db is not None:
        try:
            _session_db.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("Error closing SessionDB: %s", exc)
        _session_db = None

    _remove_lock_file()
    _remove_status_file()
    _remove_pid_file()

    try:
        from spd_analysis_engine.scripts.storage_rotator import update_project_meta
        update_project_meta(_engine_root_str, _project_name, clean_exit="true", status="stopped")
    except Exception as exc:
        logger.debug("Could not update project.meta clean_exit: %s", exc)

    logger.info("Worker exiting cleanly.")
    sys.exit(0)


def _crash_handler() -> None:
    """
    ``atexit`` fallback for unexpected termination.

    If the process exits without ``_shutdown_requested`` being set the
    session row is marked ``CRASHED`` for post-mortem analysis.
    """
    global _session_db, _session_id

    if _shutdown_requested:
        return  # normal exit path already handled everything

    logger.warning("Unexpected exit — marking session as CRASHED")

    if _session_db is not None and _session_id is not None:
        try:
            _session_db.close_session(_session_id, status="CRASHED")
        except Exception:  # noqa: BLE001
            pass

    if _session_db is not None:
        try:
            _session_db.close()
        except Exception:  # noqa: BLE001
            pass

    _remove_status_file()
    _remove_pid_file()


def _install_signal_handlers() -> None:
    """Register OS signal handlers for graceful shutdown."""
    signal.signal(signal.SIGINT, _clean_shutdown)

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _clean_shutdown)

    # Windows: Supervisor sends CTRL_BREAK_EVENT to the worker's process group
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _clean_shutdown)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Event bundle handler (Phase 2 & Phase 4)
# ---------------------------------------------------------------------------

def _handle_event_bundle(bundle: dict[str, Any]) -> None:
    """
    Callback dispatched by ``EventBundler`` when a quiet period elapses.

    Processes all files touched during the burst:
    1. Computes unified diffs against baseline cache.
    2. Updates the baseline cache for subsequent bursts.
    3. Records an ``EDIT`` event in ``SessionDB``.
    4. Records individual file patches in ``SessionDB``.
    5. Writes an atomic combined ``.patch`` file to ``current/<slug>/patches/event_<id>.patch``.
    6. Micro-commits burst changes to ``ShadowGit`` if enabled.
    7. Emits ``event_bundled`` JSON log to ``worker.log``.
    """
    global _session_db, _session_id, _diff_calc, _patches_dir, _target_path_str, _shadow_git

    if _session_db is None or _session_id is None or _diff_calc is None:
        logger.warning("Dropped event bundle: session_db or diff_calc not initialized")
        return

    files_touched = bundle.get("files_touched", [])
    if not files_touched:
        return

    first_file = bundle.get("first_file")
    duration_s = bundle.get("duration_s", 0.0)

    # Compute diffs and update baseline for all files in bundle
    file_diffs: list[tuple[str, str]] = []
    target_root = Path(_target_path_str)

    for rel_path in files_touched:
        try:
            diff_text = _diff_calc.compute_diff(rel_path)
            file_diffs.append((rel_path, diff_text))

            # Read current content to update baseline for subsequent bursts
            abs_path = target_root / Path(rel_path)
            if abs_path.is_file():
                try:
                    curr_content = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    curr_content = None
            else:
                curr_content = None

            _diff_calc.update_baseline(rel_path, curr_content)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error computing diff / updating baseline for %s: %s", rel_path, exc)

    # Record event in SessionDB
    summary = f"Burst edit across {len(files_touched)} file(s) ({duration_s:.1f}s)"
    try:
        event_id = _session_db.record_event(
            session_id=_session_id,
            event_type="EDIT",
            first_file=first_file,
            summary=summary,
        )
        if _event_bundler:
            _event_bundler.record_action("BURST", first_file or "workspace", details=f"Burst #{event_id} ({len(files_touched)} files)")
            _write_status_file()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to record event in SessionDB: %s", exc)
        return

    # Record patches in DB and collect for disk .patch file
    combined_patch_lines: list[str] = []
    for rel_path, diff_text in file_diffs:
        try:
            _session_db.record_patch(event_id, rel_path, diff_text if diff_text else None)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to record patch in DB for %s: %s", rel_path, exc)
        if diff_text:
            combined_patch_lines.append(diff_text)

    # Write combined .patch file to current/<slug>/patches/event_<id>.patch
    if _patches_dir is not None:
        try:
            _patches_dir.mkdir(parents=True, exist_ok=True)
            patch_file = _patches_dir / f"event_{event_id}.patch"
            if combined_patch_lines:
                patch_content = "\n".join(p.rstrip("\n") for p in combined_patch_lines) + "\n"
            else:
                patch_content = f"# Event {event_id}: No text diff detected\n"
            patch_file.write_text(patch_content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to write patch file for event %d: %s", event_id, exc)

    # Create ShadowGit micro-commit if enabled
    if _shadow_git is not None:
        try:
            commit_hash = _shadow_git.commit_burst(
                event_id=event_id,
                first_file=first_file,
                summary=summary,
                files_touched=files_touched,
            )
            if commit_hash:
                _session_db.record_event(
                    session_id=_session_id,
                    event_type="GIT",
                    first_file=first_file,
                    summary=f"Micro-commit {commit_hash[:7]}: {summary}",
                )
                logger.info("ShadowGit burst committed | hash=%s", commit_hash[:7])

                # Update patches in session.db with dedicated Shadow Git version delta diffs
                for rel_p in files_touched:
                    try:
                        delta = _shadow_git.get_delta_diff(rel_p, commit_hash=commit_hash)
                        if delta:
                            _session_db.update_patch_diff(event_id, rel_p, delta)
                    except Exception as e_delta:
                        logger.debug("Could not compute delta diff for %s: %s", rel_p, e_delta)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to create ShadowGit micro-commit: %s", exc)


    # Emit JSON event to worker.log
    _emit_json(
        "event_bundled",
        {
            "event_id": event_id,
            "first_file": first_file,
            "files_count": len(files_touched),
            "files": files_touched,
            "duration_s": duration_s,
            "patches_written": len(combined_patch_lines),
        },
    )

    logger.info(
        "[BURST_SEALED] Event bundled | id=%d | files=%d | entrypoint=%s | patches=%d",
        event_id, len(files_touched), first_file, len(combined_patch_lines),
    )


# ---------------------------------------------------------------------------
# Heartbeat loop
# ---------------------------------------------------------------------------

def _run_heartbeat(interval: float = 2.0) -> None:
    """
    Main blocking loop: maintain live status and check IPC commands.

    Zero-Idle Logging Policy:
    Heartbeat ticks are NOT written to worker.log to ensure 0 bytes added
    when the IDE sits idle. worker.status.json is updated in-place for live metrics.
    """
    logger.info("Worker monitoring loop started (interval=%.1fs)", interval)
    while not _shutdown_requested:
        _write_status_file()

        elapsed = 0.0
        while elapsed < interval and not _shutdown_requested:
            # Check for IPC seal_burst.cmd (Lap button)
            if _pid_file is not None:
                seal_cmd = _pid_file.parent / "seal_burst.cmd"
                if seal_cmd.exists():
                    try:
                        seal_cmd.unlink()
                    except Exception:
                        pass
                    if _event_bundler is not None:
                        _event_bundler.flush_burst_now()
                        _write_status_file()
            time.sleep(0.1)
            elapsed += 0.1


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project_worker",
        description="SPD Analysis Engine — isolated project monitoring worker",
    )
    parser.add_argument(
        "--name", required=True, metavar="PROJECT_NAME",
        help="Human-readable project name (used as directory slug)",
    )
    parser.add_argument(
        "--path", required=True, metavar="TARGET_PATH",
        help="Absolute path to the project directory being monitored",
    )
    parser.add_argument(
        "--scaffold", action="store_true", default=False,
        help="Automatically create the target directory if it does not exist",
    )
    parser.add_argument(
        "--engine-root", default=str(_ENGINE_ROOT_DEFAULT), metavar="ENGINE_ROOT",
        help=f"Root of the SPD Analysis Engine (default: {_ENGINE_ROOT_DEFAULT})",
    )
    parser.add_argument(
        "--heartbeat-interval", type=float, default=2.0, metavar="SECONDS",
        help="Heartbeat emit interval in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--debounce", type=float, default=3.5, metavar="SECONDS",
        help="Sliding debounce quiet period in seconds (default: 3.5)",
    )
    parser.add_argument(
        "--track-reads", action=argparse.BooleanOptionalAction, default=True,
        help="Track file read/analysis handles via psutil (default: True)",
    )
    parser.add_argument(
        "--track-exec", action=argparse.BooleanOptionalAction, default=True,
        help="Track terminal execution commands (default: True)",
    )
    parser.add_argument(
        "--shadow-git", action=argparse.BooleanOptionalAction, default=True,
        help="Maintain shadow git micro-versioning (default: True)",
    )
    parser.add_argument(
        "--git-init-primary", action="store_true", default=False,
        help="Initialize primary git repo if missing (default: False)",
    )
    parser.add_argument(
        "--ide-profile", default="antigravity",
        choices=["antigravity", "cursor", "windsurf", "claude_code", "custom"],
        help="Monitored AI environment profile (default: antigravity)",
    )
    parser.add_argument(
        "--ide-custom-marker", default=None,
        help="Custom process marker or executable name when using custom profile",
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _unhandled_exception_handler(exc_type, exc_value, exc_traceback) -> None:
    """Log any uncaught exception before process termination."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("UNHANDLED EXCEPTION in worker:", exc_info=(exc_type, exc_value, exc_traceback))


def main(argv: list[str] | None = None) -> None:
    """
    Bootstrap and run the project worker.

    Parameters
    ----------
    argv : Argument vector.  Defaults to ``sys.argv[1:]`` when ``None``.
    """
    global _session_db, _session_id, _pid_file, _status_file, _lock_file
    global _start_time, _project_name, _target_path_str, _engine_root_str
    global _diff_calc, _event_bundler, _project_watcher, _patches_dir
    global _powers, _shadow_git, _proc_inspector, _exec_tracker

    args = _build_arg_parser().parse_args(argv)
    _project_name = args.name
    _start_time = time.monotonic()
    _powers = {
        "track_edits": True,
        "track_reads": bool(args.track_reads),
        "track_exec": bool(args.track_exec),
        "shadow_git": bool(args.shadow_git),
        "ide_profile": args.ide_profile,
        "ide_custom_marker": args.ide_custom_marker or "",
    }

    # ------------------------------------------------------------------ #
    # 1. Resolve paths
    # ------------------------------------------------------------------ #
    engine_root = Path(args.engine_root).resolve()
    target_path = Path(args.path).resolve()
    _target_path_str = str(target_path)
    _engine_root_str = str(engine_root)

    # ------------------------------------------------------------------ #
    # 1b. Redirect stdout and stderr cleanly to worker.log
    # ------------------------------------------------------------------ #
    from spd_analysis_engine.scripts.storage_rotator import _slug
    proj_dir_preview = engine_root / "current" / _slug(args.name)
    proj_dir_preview.mkdir(parents=True, exist_ok=True)
    log_path = proj_dir_preview / "worker.log"
    
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file
    sys.excepthook = _unhandled_exception_handler

    # Re-wire root logger handlers to target worker.log
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
    file_handler = logging.StreamHandler(log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [WORKER] %(levelname)-8s %(message)s", datefmt="%H:%M:%S"))
    logging.root.addHandler(file_handler)
    logging.root.setLevel(logging.INFO)

    logger.info(
        "Worker starting | project=%s | target=%s | engine_root=%s | powers=%s",
        args.name, target_path, engine_root, _powers,
    )

    # ------------------------------------------------------------------ #
    # 2. Auto-scaffold target directory & optional primary git init
    # ------------------------------------------------------------------ #
    if not target_path.exists():
        if args.scaffold:
            logger.info("Scaffolding target directory: %s", target_path)
            target_path.mkdir(parents=True, exist_ok=True)
        else:
            logger.error(
                "Target path does not exist: %s  "
                "Use --scaffold to create it automatically.",
                target_path,
            )
            sys.exit(1)

    if args.git_init_primary:
        if ShadowGit.init_primary_repo(target_path):
            logger.info("Primary git repo initialized in %s", target_path)

    # ------------------------------------------------------------------ #
    # 3. Initialise current/<slug>/ via StorageRotator
    # ------------------------------------------------------------------ #
    rotator = StorageRotator(engine_root)
    proj_dir = rotator.init_project(args.name, str(target_path))
    logger.info("Project dir initialised: %s", proj_dir)

    # ------------------------------------------------------------------ #
    # 4. Open SessionDB (WAL mode)
    # ------------------------------------------------------------------ #
    db_path = proj_dir / "session.db"
    _session_db = SessionDB(db_path)
    _session_id = _session_db.create_session(args.name, str(target_path))
    logger.info("Session created | id=%s | db=%s", _session_id, db_path)

    # ------------------------------------------------------------------ #
    # 5. Write worker.pid  (enriched with target_path + engine_root + powers)
    # ------------------------------------------------------------------ #
    _pid_file = proj_dir / "worker.pid"
    _write_pid_file(_pid_file)

    # ------------------------------------------------------------------ #
    # 5b. Write session.lock (sentinel for crash / power-cut detection)
    # ------------------------------------------------------------------ #
    _lock_file = proj_dir / "session.lock"
    _write_lock_file(_lock_file)

    # ------------------------------------------------------------------ #
    # 6. Set status file path (written on first heartbeat)
    # ------------------------------------------------------------------ #
    _status_file = proj_dir / "worker.status.json"

    # ------------------------------------------------------------------ #
    # 7. Register signal handlers + atexit crash guard
    # ------------------------------------------------------------------ #
    _install_signal_handlers()
    atexit.register(_crash_handler)

    # ------------------------------------------------------------------ #
    # 7b. Phase 2: Baseline cache, Event bundler, and File watcher
    # ------------------------------------------------------------------ #
    _patches_dir = proj_dir / "patches"
    _patches_dir.mkdir(parents=True, exist_ok=True)

    baseline_dir = proj_dir / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)

    _diff_calc = DiffCalculator(baseline_dir=baseline_dir, target_dir=target_path)
    _diff_calc.capture_baseline(target_path)

    def _on_fs_activity() -> None:
        if _exec_tracker is not None:
            _exec_tracker.trigger_fast_poll(args.debounce + 1.0)

    _event_bundler = EventBundler(
        callback=_handle_event_bundle,
        quiet_period=args.debounce,
        on_activity=_on_fs_activity,
    )
    _project_watcher = ProjectWatcher(target_path=target_path, bundler=_event_bundler)
    _project_watcher.start()
    logger.info("ProjectWatcher and EventBundler started (quiet_period=%.1fs)", args.debounce)

    # ------------------------------------------------------------------ #
    # 7c. Phase 4: Shadow Git Micro-Versioning
    # ------------------------------------------------------------------ #
    if args.shadow_git:
        shadow_repo_dir = proj_dir / "shadow_git"
        _shadow_git = ShadowGit(shadow_dir=shadow_repo_dir, target_dir=target_path)
        _shadow_git.init_repo()
        logger.info("ShadowGit initialized at %s for work-tree %s", shadow_repo_dir, target_path)

    # ------------------------------------------------------------------ #
    # 7d. Phase 4: File Read Handle Inspector (psutil)
    # ------------------------------------------------------------------ #
    if args.track_reads:
        def _on_file_read(read_info: dict[str, Any]) -> None:
            rel_path = read_info.get("file_path", "")
            proc_name = read_info.get("process_name", "unknown")
            pid = read_info.get("pid", 0)
            if _event_bundler and rel_path:
                _event_bundler.record_action("READ", rel_path, details=f"Inspected by {proc_name} (PID {pid})")
                _write_status_file()
            if _session_db is not None and _session_id is not None:
                try:
                    _session_db.record_event(
                        session_id=_session_id,
                        event_type="READ",
                        first_file=rel_path,
                        summary=f"File inspected by {proc_name} (PID {pid})",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to record READ event: %s", exc)

        _proc_inspector = ProcessInspector(
            target_path=target_path,
            on_file_read=_on_file_read,
            ide_profile=args.ide_profile,
            custom_marker=args.ide_custom_marker,
        )
        _proc_inspector.start()
        logger.info("ProcessInspector started for target %s (profile=%s)", target_path, args.ide_profile)

    # ------------------------------------------------------------------ #
    # 7e. Phase 4: Terminal Command Execution Tracker
    # ------------------------------------------------------------------ #
    if args.track_exec:
        def _on_command_executed(cmd_info: dict[str, Any]) -> None:
            cmd_str = cmd_info.get("command", "")
            exit_code = cmd_info.get("exit_code", 0)
            if _event_bundler and cmd_str:
                _event_bundler.record_action("EXEC", cmd_str, details=f"Exit code: {exit_code}")
                _write_status_file()
            logger.info("[EXEC_CAPTURED] %s (exit=%s)", cmd_str, exit_code)

        _exec_tracker = ExecutionTracker(
            target_dir=target_path,
            db=_session_db,
            session_id=_session_id,
            idle_interval=1.2,
            fast_interval=0.18,
            is_active_callback=lambda: bool(_event_bundler and _event_bundler.get_debounce_status().get("active")),
            on_command=_on_command_executed,
            on_read=_on_file_read if args.track_reads else None,
            ide_profile=args.ide_profile,
            custom_marker=args.ide_custom_marker,
        )
        _exec_tracker.start()
        logger.info("ExecutionTracker started for target %s (profile=%s, idle=1.2s, fast=0.18s)", target_path, args.ide_profile)

    # ------------------------------------------------------------------ #
    # 8. Emit startup event (→ worker.log) and begin heartbeat loop
    # ------------------------------------------------------------------ #
    _emit_json(
        "startup",
        {
            "target_path": str(target_path),
            "session_id":  _session_id,
            "db_path":     str(db_path),
            "powers":      _powers,
        },
    )
    # Write the first status file immediately so Supervisor.get_status()
    # can read session_id without waiting for the first heartbeat.
    _write_status_file()

    try:
        _run_heartbeat(interval=args.heartbeat_interval)
    except KeyboardInterrupt:
        _clean_shutdown(signal.SIGINT)


if __name__ == "__main__":
    main()
```

---

### [11/27] File: `scripts\shell_interceptor.py`
- **Lines:** 616 | **Size:** 22.1 KB | **Type:** py

```python
"""
scripts/shell_interceptor.py
============================
Terminal command execution tracking and auditing for the SPD Analysis Engine.

Tracks terminal executions in the project workspace context, recording command
strings, timestamps, durations, and exit codes into the SQLite ``events`` table
with ``event_type="EXEC"``.

Features
--------
1. Direct API recording: Programmatic entrypoint for recording executions via
   the REST API or CLI hooks.
2. Background shell process detector: Monitors child processes spawned by shells
   (PowerShell, CMD, Bash, Pwsh) operating within the workspace directory.
3. Thread-safe execution buffer and SQLite event integration.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import psutil

def scrub_tokens(text: str) -> str:
    """Sanitize passwords, personal access tokens, and API credentials from strings/URLs."""
    if not text:
        return ""
    # 1. URL basic auth: https://user:pass@host -> https://***:***@host
    s = re.sub(r'(https?://)([^:\s@]+):([^@\s]+)@', r'\1***:***@', text)
    # 2. URL single token auth: https://token@host -> https://***@host
    s = re.sub(r'(https?://)([^@/:\s]+)@', r'\1***@', s)
    # 3. GitHub personal access tokens: ghp_...
    s = re.sub(r'ghp_[A-Za-z0-9_]+', 'ghp_***', s)
    # 4. GitHub Fine-grained PATs: github_pat_...
    s = re.sub(r'github_pat_[A-Za-z0-9_]+', 'github_pat_***', s)
    # 5. Google / Gemini API keys: AIza...
    s = re.sub(r'AIza[A-Za-z0-9_\-]+', 'AIza***', s)
    return s


try:
    from scripts.ide_profiles import is_process_whitelisted, get_profile, IDE_PROFILES
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ide_profiles import is_process_whitelisted, get_profile, IDE_PROFILES
    except (ImportError, ModuleNotFoundError):
        def is_process_whitelisted(proc: Any, profile_name: str = "antigravity", custom_marker: str | None = None) -> bool:
            return True
        IDE_PROFILES = {}



def detect_executor(
    cmd_str: str,
    proc: psutil.Process | None = None,
    ide_profile: str = "antigravity",
) -> str:
    """
    Differentiate interactive human terminal commands from automated AI agent runs.
    Returns 'AI_AGENT' or 'HUMAN_DEV'.
    """
    if not cmd_str:
        return "HUMAN_DEV"

    cmd_low = cmd_str.lower().strip()

    # 1. Automated AI tool and headless script flags
    ai_patterns = [
        " -c ", " -c\"", " -c'",
        " -e ", " -e\"", " -e'",
        "--eval",
        "--headless",
        "-m py_compile",
        "-m unittest",
        "eslint --fix",
        "prettier --write",
        "autopep8",
        "black",
        "node --check",
        "test_runner",
        "eval_",
        "tmp_",
        "/tmp/",
        "\\tmp\\",
        "agent_",
        "scratch/",
        "scratch\\",
        "--non-interactive",
        "--ci",
    ]
    for pattern in ai_patterns:
        if pattern in cmd_low:
            return "AI_AGENT"

    # 2. PowerShell / CMD Headless Flag Detection:
    # Match flags: -command, -encodedcommand, -file, /c
    headless_flags = ("-command", "-encodedcommand", "-file")
    if any(f in cmd_low for f in headless_flags):
        return "AI_AGENT"

    if cmd_low.startswith("/c ") or " /c " in cmd_low or " /c\"" in cmd_low:
        return "AI_AGENT"

    if ("powershell" in cmd_low or "pwsh" in cmd_low) and any(
        f in cmd_low for f in ("-c ", " -c", "-command", "-encodedcommand", "-file")
    ):
        return "AI_AGENT"

    # 3. Check process tree markers and IDE profile if proc is available
    if proc is not None:
        try:
            tree = [proc]
            try:
                tree.extend(proc.parents())
            except Exception:
                pass

            profile_data = IDE_PROFILES.get(ide_profile.lower(), {}) if IDE_PROFILES else {}
            profile_exes = [e.lower() for e in profile_data.get("executables", ["antigravity.exe", "antigravity"])]
            profile_markers = [m.lower() for m in profile_data.get("process_markers", ["antigravity", "gemini", "agy"])]

            for p in tree:
                try:
                    p_name = (p.name() if callable(getattr(p, "name", None)) else getattr(p, "name", "")) or ""
                    p_name_low = p_name.lower()
                    p_exe = (p.exe() if callable(getattr(p, "exe", None)) else getattr(p, "exe", "")) or ""
                    p_exe_low = p_exe.lower()
                    cmdline_raw = p.cmdline() if callable(getattr(p, "cmdline", None)) else getattr(p, "cmdline", [])
                    p_cmd = " ".join(cmdline_raw or []).lower()

                    # Check if process is the active AI IDE (e.g. Antigravity)
                    if any(p_name_low == pe or p_exe_low.endswith(pe) for pe in profile_exes):
                        return "AI_AGENT"
                    if any(m in p_cmd or m in p_name_low or m in p_exe_low for m in profile_markers):
                        return "AI_AGENT"

                    # Check generic AI agent process markers
                    if any(m in p_cmd for m in ("subagent", "agent_worker", "ai_agent", "--headless", "code-execution")):
                        return "AI_AGENT"

                    # Check pwsh/powershell running headless
                    if ("powershell" in p_name_low or "pwsh" in p_name_low) and any(
                        hf in p_cmd for hf in ("-command", "-encodedcommand", "-file", " -c ", " -c\"")
                    ):
                        return "AI_AGENT"

                except Exception:
                    pass
        except Exception:
            pass

    # 4. Interactive human dev patterns
    return "HUMAN_DEV"


if TYPE_CHECKING:
    from spd_analysis_engine.scripts.storage_rotator import SessionDB

logger = logging.getLogger(__name__)

#: Shell host process names
SHELL_HOST_NAMES: set[str] = {
    "powershell.exe", "powershell",
    "pwsh.exe", "pwsh",
    "cmd.exe", "cmd",
    "bash.exe", "bash",
    "zsh", "sh",
}

#: Common utility and IDE host processes to ignore from execution logs to prevent noise
IGNORED_COMMANDS: set[str] = {
    "conhost.exe", "wmic.exe", "tasklist.exe", "psutil",
    "code.exe", "code",
    "cursor.exe", "cursor",
    "windsurf.exe", "windsurf",
    "electron.exe", "electron",
}

#: Path substring patterns of internal IDE installations and editor extensions
IGNORED_PATH_PATTERNS: tuple[str, ...] = (
    "appdata/local/programs/microsoft vs code",
    "appdata\\local\\programs\\microsoft vs code",
    "resources/app/extensions",
    "resources\\app\\extensions",
    "resources/app/out/vs",
    "resources\\app\\out\\vs",
    ".vscode/extensions",
    ".vscode\\extensions",
    ".cursor/extensions",
    ".cursor\\extensions",
    ".windsurf/extensions",
    ".windsurf\\extensions",
)

#: Substring patterns of internal IDE tasks, background language servers, and auth daemons
IGNORED_EXEC_PATTERNS: tuple[str, ...] = (
    "shellintegration.ps1",
    "jsonservermain",
    "eslintserver",
    "eslintserver.js",
    "typescript-language-features",
    "apex-language-server",
    "git-credential-",
    "credential-manager",
    "git-credential-manager",
    "--node-ipc",
    "--clientprocessid",
)


def is_ignored_command(cmd: str) -> bool:
    """Check if command matches internal IDE background tasks, daemons, or helper tools."""
    if not cmd:
        return True
    cmd_lower = cmd.lower().strip()
    cmd_normalized = cmd_lower.replace("\\", "/")

    # 1. Check IDE paths
    for pattern in IGNORED_PATH_PATTERNS:
        if pattern.replace("\\", "/") in cmd_normalized:
            return True

    # 2. Check script names & background language server flags
    for pattern in IGNORED_EXEC_PATTERNS:
        if pattern in cmd_lower:
            return True

    # 3. Check standalone utility binaries
    first_token = cmd_lower.split()[0] if cmd_lower.split() else ""
    first_base = os.path.basename(first_token)
    if first_base in IGNORED_COMMANDS:
        return True

    # 4. Check shell host invocations without explicit user scripts or with internal flags
    if first_base in SHELL_HOST_NAMES:
        if len(cmd_lower.split()) <= 1:
            return True
        if "-noexit" in cmd_lower or "shellintegration" in cmd_lower:
            return True

    return False



def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ExecutionTracker:
    """
    Audits and records terminal executions within a workspace directory.

    Parameters
    ----------
    target_path : Path | str
        Root workspace path being monitored.
    session_db : SessionDB | None
        Open SessionDB instance for inserting EXEC event rows.
    session_id : int | None
        Active session identifier.
    poll_interval : float
        Interval for background shell process scanning (default: 1.0s).
    """

    def __init__(
        self,
        target_path: Path | str | None = None,
        session_db: SessionDB | None = None,
        session_id: int | None = None,
        poll_interval: float = 1.2,
        idle_interval: float | None = None,
        fast_interval: float = 0.18,
        is_active_callback: Callable[[], bool] | None = None,
        target_dir: Path | str | None = None,
        db: SessionDB | None = None,
        on_command: Callable[[dict[str, Any]], None] | None = None,
        on_read: Callable[[dict[str, Any]], None] | None = None,
        ide_profile: str = "antigravity",
        custom_marker: str | None = None,
    ) -> None:
        real_target = target_path if target_path is not None else target_dir
        if real_target is None:
            real_target = Path.cwd()
        self.target_path = Path(real_target).resolve()
        self.session_db = session_db if session_db is not None else db
        self.session_id = session_id

        self.idle_interval = idle_interval if idle_interval is not None else poll_interval
        self.poll_interval = self.idle_interval
        self.fast_interval = fast_interval
        self.is_active_callback = is_active_callback
        self._fast_poll_until: float = 0.0

        self.on_command = on_command
        self.on_read = on_read
        self.ide_profile = ide_profile
        self.custom_marker = custom_marker

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Track PIDs already captured to avoid re-logging
        self._seen_pids: set[int] = set()
        self._recent_executions: list[dict[str, Any]] = []

    def trigger_fast_poll(self, duration_s: float = 3.5) -> None:
        """Temporarily accelerate polling to fast_interval (150ms-200ms) for duration_s."""
        expiry = time.monotonic() + duration_s
        with self._lock:
            if expiry > self._fast_poll_until:
                self._fast_poll_until = expiry

    @property
    def is_fast_polling(self) -> bool:
        """Return True if fast polling (150ms-200ms) is active."""
        if time.monotonic() < self._fast_poll_until:
            return True
        if self.is_active_callback:
            try:
                return bool(self.is_active_callback())
            except Exception:
                return False
        return False

    def set_session(self, session_db: SessionDB, session_id: int) -> None:
        """Bind or update active SessionDB reference."""
        with self._lock:
            self.session_db = session_db
            self.session_id = session_id

    def _detect_file_reads_from_cmd(self, cmd_str: str) -> list[str]:
        """Parse command line strings for referenced workspace files and emit READ events."""
        if not cmd_str or not self.target_path.exists():
            return []

        import shlex
        try:
            tokens = shlex.split(cmd_str, posix=False)
        except Exception:
            tokens = cmd_str.split()

        flat_tokens = [tok.strip(" ,;()[]{}<>\"'") for tok in tokens if tok.strip()]

        detected_files: list[str] = []
        for tok in flat_tokens:
            candidate = tok.strip(" ,;()[]{}<>\"'")
            if not candidate or len(candidate) < 2:
                continue
            if (candidate.startswith("-") or candidate.startswith("/")) and not (candidate.startswith("./") or candidate.startswith(".//")):
                if not ("." in candidate or "/" in candidate or "\\" in candidate):
                    continue
                candidate = candidate.lstrip("-")

            try:
                p = Path(candidate)
                resolved: Path | None = None
                if p.is_absolute():
                    if self._is_path_inside_target(str(p)):
                        resolved = p.resolve()
                else:
                    cand_path = (self.target_path / p).resolve()
                    if cand_path.exists() and cand_path.is_file():
                        resolved = cand_path

                if resolved and resolved.exists() and resolved.is_file():
                    rel_p = str(resolved.relative_to(self.target_path)).replace("\\", "/")
                    try:
                        from scripts.watcher_proc import IGNORED_DIRS, IGNORED_EXTENSIONS
                    except ImportError:
                        try:
                            from spd_analysis_engine.scripts.watcher_proc import IGNORED_DIRS, IGNORED_EXTENSIONS
                        except ImportError:
                            IGNORED_DIRS = {".git", ".spd", "node_modules", "__pycache__", ".venv"}
                            IGNORED_EXTENSIONS = {".pyc", ".tmp", ".log"}
                    parts = set(resolved.parts)
                    if not parts.intersection(IGNORED_DIRS) and resolved.suffix.lower() not in IGNORED_EXTENSIONS:
                        detected_files.append(rel_p)
            except Exception:
                continue

        return list(dict.fromkeys(detected_files))

    def record_execution(
        self,
        command: str,
        duration_s: float = 0.0,
        exit_code: int = 0,
        cwd: str | None = None,
        executor: str | None = None,
    ) -> int | None:
        """
        Record a command execution directly into SessionDB and the recent buffer.

        Parameters
        ----------
        command : str
            Full command line string executed.
        duration_s : float
            Execution duration in seconds.
        exit_code : int
            Process exit status code (0 = success).
        cwd : str | None
            Working directory where command executed.
        executor : str | None
            Execution initiator ('AI_AGENT' or 'HUMAN_DEV'). Auto-detected if None.

        Returns
        -------
        int | None
            The newly inserted ``events.id`` or ``None``.
        """
        raw_cmd = str(command).strip()
        if not raw_cmd or is_ignored_command(raw_cmd):
            return None

        clean_cmd = scrub_tokens(raw_cmd)
        if not executor:
            executor = detect_executor(clean_cmd)

        event_id = None
        summary = f"[{executor}] cmd: '{clean_cmd}' | exit={exit_code} | duration={duration_s:.2f}s"
        ts = _utcnow_iso()

        with self._lock:
            if self.session_db is not None and self.session_id is not None:
                try:
                    event_id = self.session_db.record_event(
                        session_id=self.session_id,
                        event_type="EXEC",
                        first_file=clean_cmd,
                        summary=summary,
                        executor=executor,
                    )
                except Exception as exc:
                    logger.error("Failed to insert EXEC event in SessionDB: %s", exc)

            record = {
                "id": event_id,
                "event_type": "EXEC",
                "command": clean_cmd,
                "duration_s": round(duration_s, 2),
                "exit_code": exit_code,
                "cwd": cwd or str(self.target_path),
                "timestamp": ts,
                "summary": summary,
                "executor": executor,
            }
            self._recent_executions.append(record)
            logger.info("Recorded EXEC event: %s", summary)

        if self.on_command:
            try:
                self.on_command(record)
            except Exception as exc:
                logger.debug("Error in on_command callback: %s", exc)

        # Detect referenced files in workspace and record READ events
        referenced_files = self._detect_file_reads_from_cmd(clean_cmd)
        for ref_f in referenced_files:
            try:
                read_record = {
                    "event_type": "READ",
                    "file_path": ref_f,
                    "process_name": "cmd_reference",
                    "pid": os.getpid(),
                    "timestamp": ts,
                    "summary": f"File referenced in command: {clean_cmd}",
                }
                if self.session_db is not None and self.session_id is not None:
                    self.session_db.record_event(
                        session_id=self.session_id,
                        event_type="READ",
                        first_file=ref_f,
                        summary=f"Inspected by command: {clean_cmd}",
                    )
                if self.on_read:
                    self.on_read(read_record)
            except Exception as read_exc:
                logger.debug("Failed recording command-derived READ event: %s", read_exc)

        return event_id

    def _is_path_inside_target(self, path_str: str) -> bool:
        """Check if *path_str* is inside self.target_path."""
        try:
            p = Path(path_str).resolve()
            p.relative_to(self.target_path)
            return True
        except (ValueError, OSError):
            return False

    def _scan_processes_once(self) -> None:
        """Inspect running child processes of shells located inside target_path."""
        my_pid = os.getpid()

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            if self._stop_event.is_set():
                break

            try:
                pid = proc.info.get("pid")
                if not pid or pid == my_pid:
                    continue

                with self._lock:
                    if pid in self._seen_pids:
                        continue

                name = proc.info.get("name") or ""
                if name.lower() in IGNORED_COMMANDS:
                    continue

                try:
                    exe = proc.exe()
                    if exe and is_ignored_command(exe):
                        continue
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    pass

                # Check process working directory
                try:
                    cwd = proc.cwd()
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    cwd = None

                if not cwd or not self._is_path_inside_target(cwd):
                    continue

                # Verify whether process or its parents match whitelisted IDE profile
                if not is_process_whitelisted(proc, self.ide_profile, self.custom_marker):
                    continue

                # Captured a command running inside target_path!
                cmdline = proc.info.get("cmdline") or [name]
                cmd_str = scrub_tokens(" ".join(cmdline))

                # Skip internal engine workers and IDE background extensions
                if "project_worker.py" in cmd_str or "web_server.py" in cmd_str or is_ignored_command(cmd_str):
                    continue

                with self._lock:
                    if len(self._seen_pids) > 2000:
                        self._seen_pids.clear()
                    self._seen_pids.add(pid)

                executor = detect_executor(cmd_str, proc, ide_profile=self.ide_profile)

                t0 = time.monotonic()
                # Wait briefly for quick commands to complete so we capture exit code
                exit_code = 0
                try:
                    proc.wait(timeout=0.2)
                    exit_code = proc.returncode if proc.returncode is not None else 0
                except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                    pass

                duration = time.monotonic() - t0
                self.record_execution(
                    command=cmd_str,
                    duration_s=duration,
                    exit_code=exit_code,
                    cwd=cwd,
                    executor=executor,
                )

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
            except Exception as exc:
                logger.debug("Error inspecting process execution: %s", exc)
                continue

    def _run_loop(self) -> None:
        logger.info(
            "ExecutionTracker started observing %s (idle=%.2fs, fast=%.2fs)",
            self.target_path,
            self.idle_interval,
            self.fast_interval,
        )
        while not self._stop_event.is_set():
            self._scan_processes_once()
            current_interval = self.fast_interval if self.is_fast_polling else self.idle_interval
            self._stop_event.wait(current_interval)

    def start(self) -> None:
        """Start the background process execution scanner."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop and join the tracker thread."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("ExecutionTracker stopped on %s", self.target_path)

    def get_recent_executions(self, clear: bool = False) -> list[dict[str, Any]]:
        """Return buffered execution records."""
        with self._lock:
            records = list(self._recent_executions)
            if clear:
                self._recent_executions.clear()
            return records
```

---

### [12/27] File: `scripts\storage_rotator.py`
- **Lines:** 1117 | **Size:** 42.71 KB | **Type:** py

```python
"""
scripts/storage_rotator.py
==========================
SQLite WAL-mode database manager and lifecycle directory rotator for the
SPD Analysis Engine.

Classes
-------
SessionDB
    Opens / creates ``<project_current_dir>/session.db`` with WAL mode and
    exposes typed helper methods for inserting and querying sessions, events,
    and patches. All writes are wrapped in explicit transactions; WAL is
    flushed (checkpointed) on close to prevent data loss.

StorageRotator
    Manages the three-tier directory lifecycle:

        current/<slug>/   – live working tree for an active worker
        last_run/<slug>/  – snapshot of the most-recently completed session
        history/<slug>/run_<ts>/ – permanent archive of older sessions

    ``archive_project(name)`` is called by the orchestrator when a worker
    exits. ``init_project(name, path)`` is called at worker startup.

Design notes
------------
* All filesystem operations use ``pathlib.Path`` for Windows / POSIX compat.
* ``shutil.move`` is used for atomic-ish directory moves on the same volume.
* SQLite is opened with ``check_same_thread=False`` so the connection can be
  used from a single worker process that may spawn helper threads later.
* WAL checkpoint mode is ``TRUNCATE`` on close: keeps the WAL file small and
  avoids stale reader locks.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from scripts.trash_manager import _force_rmtree
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.trash_manager import _force_rmtree
    except (ImportError, ModuleNotFoundError):
        def _force_rmtree(path: Path | str) -> None:
            p = Path(path)
            if not p.exists():
                return
            try:
                if p.is_dir():
                    for root, dirs, files in os.walk(p):
                        for f in files:
                            try:
                                os.chmod(os.path.join(root, f), stat.S_IWRITE | stat.S_IWUSR)
                            except Exception:
                                pass
                shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass

try:
    from scripts.event_bundler import suppress_file_path
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.event_bundler import suppress_file_path
    except (ImportError, ModuleNotFoundError):
        def suppress_file_path(path: Any, duration_s: float = 5.0) -> None:
            pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (no microseconds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(name: str) -> str:
    """
    Convert a project name to a filesystem-safe slug.

    Replaces spaces and special characters with underscores and lower-cases
    the result so paths are predictable across platforms.

    Examples
    --------
    >>> _slug("My Cool Project!")
    'my_cool_project_'
    """
    import re
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name).lower()


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
            for k, v in kwargs.items():
                if v is not None:
                    data[str(k).strip()] = str(v).strip()
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
        for k, v in kwargs.items():
            if v is not None:
                data[str(k).strip()] = str(v).strip()
        lines = [f"{k}={v}" for k, v in data.items()]
        suppress_file_path(str(meta_file), duration_s=5.0)
        meta_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        suppress_file_path(str(meta_file), duration_s=5.0)


def consolidate_project_history(engine_root: Path | str, project_name: str) -> Path | None:
    """
    Consolidate all historical session.db files and patch files for a project across
    history/<slug>/run_*, last_run/<slug>, and current/<slug> into a single cumulative
    database. Idempotent and crash-resilient.

    Returns the path to the canonical session.db.
    """
    slug = _slug(project_name)
    engine_p = Path(engine_root).resolve()

    current_dir = engine_p / "current" / slug
    last_run_dir = engine_p / "last_run" / slug
    hist_dir = engine_p / "history" / slug

    run_dirs: list[Path] = []
    if hist_dir.exists():
        for d in sorted(hist_dir.iterdir()):
            if d.is_dir() and d.name.startswith("run_"):
                if (d / "session.db").exists():
                    run_dirs.append(d)

    if last_run_dir.exists() and (last_run_dir / "session.db").exists():
        run_dirs.append(last_run_dir)

    if current_dir.exists() and (current_dir / "session.db").exists():
        run_dirs.append(current_dir)

    if not run_dirs:
        return None

    target_dir = current_dir if current_dir.exists() else last_run_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target_db_path = target_dir / "session.db"
    target_patches_dir = target_dir / "patches"
    target_patches_dir.mkdir(parents=True, exist_ok=True)

    target_db = SessionDB(target_db_path)
    target_conn = target_db.conn
    assert target_conn is not None

    target_cur = target_conn.cursor()
    target_cur.execute("SELECT id, project_name, start_time FROM sessions")
    existing_sessions = {(r[1], r[2]): r[0] for r in target_cur.fetchall()}

    target_cur.execute("SELECT id, timestamp, event_type, first_file_touched, summary FROM events")
    existing_events = {(r[1], r[2], r[3], r[4]): r[0] for r in target_cur.fetchall()}

    for src_dir in run_dirs:
        src_db_path = src_dir / "session.db"
        if src_db_path.resolve() == target_db_path.resolve():
            continue

        try:
            src_conn = sqlite3.connect(f"file:{src_db_path}?mode=ro", uri=True)
            src_conn.row_factory = sqlite3.Row
            src_cur = src_conn.cursor()

            src_cur.execute("SELECT id, project_name, target_path, start_time, end_time, status FROM sessions ORDER BY id ASC")
            src_sessions = src_cur.fetchall()
            session_map: dict[int, int] = {}
            for s in src_sessions:
                s_key = (s["project_name"], s["start_time"])
                if s_key in existing_sessions:
                    session_map[s["id"]] = existing_sessions[s_key]
                else:
                    target_cur.execute(
                        "INSERT INTO sessions (project_name, target_path, start_time, end_time, status) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (s["project_name"], s["target_path"], s["start_time"], s["end_time"], s["status"]),
                    )
                    new_sid = target_cur.lastrowid
                    existing_sessions[s_key] = new_sid
                    session_map[s["id"]] = new_sid

            src_cur.execute("PRAGMA table_info(events)")
            cols = [c["name"] for c in src_cur.fetchall()]
            has_ai = "ai_summary" in cols
            has_exec = "executor" in cols

            select_cols = ["id", "session_id", "event_type", "timestamp", "first_file_touched", "summary"]
            if has_ai:
                select_cols.append("ai_summary")
            if has_exec:
                select_cols.append("executor")

            src_cur.execute(f"SELECT {', '.join(select_cols)} FROM events ORDER BY id ASC")
            src_events = src_cur.fetchall()

            for ev in src_events:
                ev_key = (ev["timestamp"], ev["event_type"], ev["first_file_touched"], ev["summary"])
                if ev_key in existing_events:
                    continue

                mapped_sid = session_map.get(ev["session_id"])
                if mapped_sid is None:
                    mapped_sid = list(existing_sessions.values())[-1] if existing_sessions else 1

                ai_summary = ev["ai_summary"] if has_ai else None
                executor = ev["executor"] if has_exec else None
                target_cur.execute(
                    "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary, ai_summary, executor) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (mapped_sid, ev["event_type"], ev["timestamp"], ev["first_file_touched"], ev["summary"], ai_summary, executor),
                )
                new_eid = target_cur.lastrowid
                existing_events[ev_key] = new_eid

                src_cur.execute("SELECT file_path, diff_content FROM patches WHERE event_id = ?", (ev["id"],))
                for p in src_cur.fetchall():
                    target_cur.execute(
                        "INSERT INTO patches (event_id, file_path, diff_content) VALUES (?, ?, ?)",
                        (new_eid, p["file_path"], p["diff_content"]),
                    )

                src_patch_file = src_dir / "patches" / f"event_{ev['id']}.patch"
                if src_patch_file.exists():
                    target_patch_file = target_patches_dir / f"event_{new_eid}.patch"
                    try:
                        shutil.copy2(src_patch_file, target_patch_file)
                    except Exception:
                        pass

            target_conn.commit()
            src_conn.close()
        except Exception as exc:
            logger.warning("Error consolidating %s: %s", src_db_path, exc)

    target_db.checkpoint()
    target_db.close()
    return target_db_path


# ---------------------------------------------------------------------------
# SessionDB
# ---------------------------------------------------------------------------

class SessionDB:
    """
    Manages a SQLite database for a single project session.

    The database lives at ``<project_current_dir>/session.db`` and is opened
    in WAL journal mode so concurrent readers and a single writer can coexist
    without blocking each other, and so that an abrupt process death cannot
    corrupt already-committed data.

    Schema
    ------
    sessions  – one row per monitoring session (start → stop lifecycle)
    events    – file-system / git events captured during a session
    patches   – raw diff content associated with EDIT events

    Parameters
    ----------
    db_path : Path | str
        Absolute path to the ``session.db`` file that will be created or
        opened.  Parent directories must already exist.

    Usage
    -----
    ::

        db = SessionDB(Path("current/my_proj/session.db"))
        sid = db.create_session("my_proj", "/workspace/my_proj")
        eid = db.record_event(sid, "EDIT", "main.py", "Modified main()")
        db.record_patch(eid, "main.py", "- old\\n+ new")
        db.close_session(sid)
        db.close()
    """

    # DDL statements executed once on first open
    _SCHEMA_SQL = """
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous   = NORMAL;
    PRAGMA foreign_keys  = ON;

    CREATE TABLE IF NOT EXISTS sessions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT    NOT NULL,
        target_path  TEXT    NOT NULL,
        start_time   TEXT    NOT NULL,
        end_time     TEXT,
        status       TEXT    NOT NULL DEFAULT 'ACTIVE'
        -- status ∈ {ACTIVE, STOPPED, CRASHED}
    );

    CREATE TABLE IF NOT EXISTS events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id        INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        event_type        TEXT    NOT NULL,
        -- event_type ∈ {EDIT, READ, EXEC, GIT}
        timestamp         TEXT    NOT NULL,
        first_file_touched TEXT,
        summary           TEXT,
        ai_summary        TEXT,
        executor          TEXT
    );

    CREATE TABLE IF NOT EXISTS patches (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        file_path    TEXT    NOT NULL,
        diff_content TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_events_session   ON events(session_id);
    CREATE INDEX IF NOT EXISTS idx_patches_event    ON patches(event_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_name);
    """

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._open()

    @property
    def conn(self) -> sqlite3.Connection | None:
        """Return the underlying SQLite connection."""
        return self._conn

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open(self) -> None:
        """Open (or create) the SQLite database and apply the schema."""
        logger.debug("Opening session DB at %s", self._path)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage transactions manually
        )
        self._conn.row_factory = sqlite3.Row
        # Apply schema (idempotent – IF NOT EXISTS guards all DDL)
        self._conn.executescript(self._SCHEMA_SQL)
        # Idempotent column migration for existing databases
        try:
            self._conn.execute("ALTER TABLE events ADD COLUMN ai_summary TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE events ADD COLUMN executor TEXT")
        except sqlite3.OperationalError:
            pass
        logger.info("SessionDB ready: %s", self._path)

    def _execute(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        commit: bool = True,
    ) -> sqlite3.Cursor:
        """
        Execute a single statement inside an explicit transaction.

        Parameters
        ----------
        sql     : SQL string with ``?`` placeholders.
        params  : Tuple of bind values.
        commit  : If ``True`` (default), ``COMMIT`` immediately after.
                  Pass ``False`` when batching multiple statements.
        """
        assert self._conn is not None, "Database is closed"
        cur = self._conn.cursor()
        if commit:
            cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(sql, params)
            if commit:
                self._conn.execute("COMMIT")
        except Exception:
            if commit:
                self._conn.execute("ROLLBACK")
            raise
        return cur

    # ------------------------------------------------------------------
    # Public API – sessions
    # ------------------------------------------------------------------

    def create_session(self, project_name: str, target_path: str) -> int:
        """
        Insert a new ``ACTIVE`` session row and return its primary key.

        Parameters
        ----------
        project_name : Human-readable project label.
        target_path  : Absolute path being monitored.

        Returns
        -------
        int – The ``sessions.id`` of the newly created row.
        """
        cur = self._execute(
            "INSERT INTO sessions (project_name, target_path, start_time, status) "
            "VALUES (?, ?, ?, 'ACTIVE')",
            (project_name, target_path, _utcnow_iso()),
        )
        sid = cur.lastrowid
        logger.info("Created session id=%s for project '%s'", sid, project_name)
        return sid  # type: ignore[return-value]

    def close_session(self, session_id: int, status: str = "STOPPED") -> None:
        """
        Mark a session as finished by setting ``end_time`` and ``status``.

        Parameters
        ----------
        session_id : Row ID returned by :meth:`create_session`.
        status     : One of ``STOPPED`` (clean exit) or ``CRASHED``.
        """
        self._execute(
            "UPDATE sessions SET end_time = ?, status = ? WHERE id = ?",
            (_utcnow_iso(), status, session_id),
        )
        logger.info("Closed session id=%s with status=%s", session_id, status)

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        """Return the row for *session_id*, or ``None`` if not found."""
        assert self._conn is not None
        cur = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        return cur.fetchone()

    # ------------------------------------------------------------------
    # Public API – events
    # ------------------------------------------------------------------

    def record_event(
        self,
        session_id: int,
        event_type: str,
        first_file: str | None = None,
        summary: str | None = None,
        executor: str | None = None,
    ) -> int:
        """
        Insert a new event row and return its primary key.

        Parameters
        ----------
        session_id  : Foreign key into ``sessions``.
        event_type  : One of ``EDIT``, ``READ``, ``EXEC``, ``GIT``.
        first_file  : Path of the first file touched (may be ``None``).
        summary     : Free-text description of what happened.
        executor    : Execution initiator ('AI_AGENT' or 'HUMAN_DEV' for EXEC).

        Returns
        -------
        int – The ``events.id`` of the new row.
        """
        valid_types = {"EDIT", "READ", "EXEC", "GIT"}
        if event_type not in valid_types:
            raise ValueError(f"event_type must be one of {valid_types}, got {event_type!r}")

        cur = self._execute(
            "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary, executor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, event_type, _utcnow_iso(), first_file, summary, executor),
        )
        eid = cur.lastrowid
        logger.debug("Recorded event id=%s type=%s file=%s executor=%s", eid, event_type, first_file, executor)
        return eid  # type: ignore[return-value]

    def get_events(self, session_id: int) -> list[sqlite3.Row]:
        """Return all events for the given session, ordered by timestamp."""
        assert self._conn is not None
        cur = self._conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        )
        return cur.fetchall()

    def update_event_ai_summary(self, event_id: int, ai_summary: str) -> None:
        """
        Update the AI-generated intent and functional explanation for an event.

        Parameters
        ----------
        event_id   : Primary key into ``events``.
        ai_summary : JSON string or structured summary text.
        """
        self._execute(
            "UPDATE events SET ai_summary = ? WHERE id = ?",
            (ai_summary, event_id),
        )
        logger.debug("Updated AI summary for event id=%s", event_id)

    # ------------------------------------------------------------------
    # Public API – patches
    # ------------------------------------------------------------------

    def record_patch(
        self,
        event_id: int,
        file_path: str,
        diff_content: str | None = None,
    ) -> int:
        """
        Store a raw diff associated with an EDIT event.

        Parameters
        ----------
        event_id     : Foreign key into ``events``.
        file_path    : The file whose diff is being stored.
        diff_content : Unified diff text (may be ``None`` for binary files).

        Returns
        -------
        int – The ``patches.id`` of the new row.
        """
        cur = self._execute(
            "INSERT INTO patches (event_id, file_path, diff_content) VALUES (?, ?, ?)",
            (event_id, file_path, diff_content),
        )
        pid = cur.lastrowid
        logger.debug("Recorded patch id=%s for event id=%s", pid, event_id)
        return pid  # type: ignore[return-value]

    def get_patches(self, event_id: int) -> list[sqlite3.Row]:
        """Return all patches associated with *event_id*."""
        assert self._conn is not None
        cur = self._conn.execute(
            "SELECT * FROM patches WHERE event_id = ? ORDER BY id",
            (event_id,),
        )
        return cur.fetchall()

    def update_patch_diff(self, event_id: int, file_path: str, diff_content: str) -> None:
        """Update or set diff_content for a patch row identified by event_id and file_path."""
        self._execute(
            "UPDATE patches SET diff_content = ? WHERE event_id = ? AND file_path = ?",
            (diff_content, event_id, file_path),
        )
        logger.debug("Updated delta diff for event id=%s file=%s", event_id, file_path)

    def purge_ignored_events(self) -> int:
        """Purge internal IDE EXEC noise rows from the events table."""
        try:
            try:
                from spd_analysis_engine.scripts.shell_interceptor import is_ignored_command
            except (ImportError, ModuleNotFoundError):
                from scripts.shell_interceptor import is_ignored_command
        except Exception:
            return 0

        assert self._conn is not None
        cur = self._conn.execute("SELECT id, first_file_touched, summary FROM events WHERE event_type = 'EXEC'")
        rows = cur.fetchall()
        to_delete = []
        for r in rows:
            if is_ignored_command(r["first_file_touched"] or "") or is_ignored_command(r["summary"] or ""):
                to_delete.append(r["id"])
        if to_delete:
            q = f"DELETE FROM events WHERE id IN ({','.join('?' for _ in to_delete)})"
            self._execute(q, tuple(to_delete))
            logger.info("Purged %d internal IDE EXEC noise rows from session.db", len(to_delete))
        return len(to_delete)


    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def checkpoint(self) -> None:
        """
        Force a WAL checkpoint in ``TRUNCATE`` mode.

        Calling this before process exit ensures all WAL frames are written
        back to the main database file, and the WAL file is reset to zero
        length.  This protects against data loss on abrupt termination of
        the *next* run (SQLite can still recover from an intact WAL, but
        truncating it keeps the on-disk state cleaner).
        """
        if self._conn is None:
            return
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            logger.debug("WAL checkpoint completed for %s", self._path)
        except sqlite3.Error as exc:
            logger.warning("WAL checkpoint failed: %s", exc)

    def close(self) -> None:
        """Checkpoint the WAL, then close the database connection."""
        self.checkpoint()
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("SessionDB closed: %s", self._path)

    def __enter__(self) -> "SessionDB":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# StorageRotator
# ---------------------------------------------------------------------------

class StorageRotator:
    """
    Manages the three-tier directory lifecycle for all projects.

    Tier layout (all relative to ``engine_root``)
    -----------------------------------------------
    ``current/<slug>/``
        Live working tree while the worker is running.  Contains:

        * ``session.db``   – the live SQLite database
        * ``patches/``     – staged patch files written by the worker
        * ``baseline/``    – snapshot of monitored files at session start
        * ``worker.pid``   – PID file written by project_worker.py

    ``last_run/<slug>/``
        Snapshot of the most recently *completed* session.  Replaced each
        time the same project finishes a new session.

    ``history/<slug>/run_<YYYYMMDD_HHMMSS>/``
        Permanent archive.  The previous ``last_run`` snapshot is moved here
        before ``current`` becomes the new ``last_run``.

    Parameters
    ----------
    engine_root : Path | str
        Root directory of the SPD Analysis Engine installation
        (the ``spd_analysis_engine/`` folder).

    Thread / process safety
    -----------------------
    Each worker process owns exactly one project slug.  The orchestrator
    serialises ``archive_project`` calls so no two processes race on the
    same slug directory.
    """

    def __init__(self, engine_root: Path | str) -> None:
        self.root = Path(engine_root).resolve()
        self._current = self.root / "current"
        self._last_run = self.root / "last_run"
        self._history = self.root / "history"

        # Ensure top-level tier directories exist
        for d in (self._current, self._last_run, self._history):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _project_dir(self, name: str, tier: str) -> Path:
        """Return the path for *name* under *tier* (``current``/``last_run``/etc.)."""
        return getattr(self, f"_{tier.replace('-', '_')}") / _slug(name)

    def _ensure_subdirs(self, project_dir: Path) -> None:
        """Create the expected sub-folders inside a project directory."""
        for sub in ("patches", "baseline"):
            (project_dir / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def init_project(self, name: str, target_path: str) -> Path:
        """
        Initialise the ``current/<slug>/`` working tree for a new session.

        Retains historical session.db, patches, and git metadata from prior runs
        so that project history is cumulative across restarts and resumes.

        Parameters
        ----------
        name        : Project name (converted to a slug internally).
        target_path : The directory being monitored.

        Returns
        -------
        Path – Absolute path to ``current/<slug>/``.
        """
        slug = _slug(name)
        proj_dir = self._project_dir(name, "current")
        proj_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_subdirs(proj_dir)

        last_run_dir = self._project_dir(name, "last_run")

        # 1. Consolidate any historical runs if present
        consolidate_project_history(self.root, name)

        # 2. Retain cumulative session.db if not already in current/
        curr_db = proj_dir / "session.db"
        if not curr_db.exists() and (last_run_dir / "session.db").exists():
            try:
                shutil.copy2(last_run_dir / "session.db", curr_db)
                logger.info("Restored historical session.db into %s", proj_dir)
            except Exception as exc:
                logger.warning("Failed to copy historical session.db: %s", exc)

        # 3. Retain patch files
        if (last_run_dir / "patches").exists():
            for p_file in (last_run_dir / "patches").glob("event_*.patch"):
                dest_p = proj_dir / "patches" / p_file.name
                if not dest_p.exists():
                    try:
                        shutil.copy2(p_file, dest_p)
                    except Exception:
                        pass

        # 4. Retain shadow_git repository if present
        if (last_run_dir / "shadow_git").exists() and not (proj_dir / "shadow_git").exists():
            try:
                shutil.copytree(last_run_dir / "shadow_git", proj_dir / "shadow_git")
            except Exception as exc:
                logger.debug("Could not copy shadow_git: %s", exc)

        # 5. Retain and update project.meta (preserving remote_url, etc.)
        existing_meta = read_project_meta(self.root, name)
        existing_meta["project_name"] = name
        existing_meta["target_path"] = target_path
        existing_meta["init_time"] = _utcnow_iso()
        existing_meta["clean_exit"] = "false"
        existing_meta["status"] = "active"

        # Check native git auto-discovery
        if not existing_meta.get("remote_url") or existing_meta.get("remote_url") in ("", "Not Linked", "none"):
            try:
                try:
                    from spd_analysis_engine.scripts.git_shadow import discover_native_git_remote
                except (ImportError, ModuleNotFoundError):
                    from scripts.git_shadow import discover_native_git_remote
                disc_url, disc_branch = discover_native_git_remote(target_path)
                if disc_url:
                    existing_meta["remote_url"] = disc_url
                    if not existing_meta.get("remote_branch"):
                        existing_meta["remote_branch"] = disc_branch
            except Exception:
                pass

        meta_file = proj_dir / "project.meta"
        lines = [f"{k}={v}" for k, v in existing_meta.items()]
        meta_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        logger.info("Initialised current dir for '%s' at %s (cumulative history preserved)", name, proj_dir)
        return proj_dir

    def archive_project(self, name: str) -> None:
        """
        Archive a completed project session.

        Steps
        -----
        1. If ``last_run/<slug>`` exists → move it to
           ``history/<slug>/run_<YYYYMMDD_HHMMSS>/``.
        2. Move ``current/<slug>`` → ``last_run/<slug>``.

        This is intentionally idempotent: if ``current/<slug>`` does not
        exist (e.g. worker was never started) the call is a no-op.

        Parameters
        ----------
        name : Project name (same slug rules as :meth:`init_project`).
        """
        slug = _slug(name)
        current_dir = self._current / slug
        last_run_dir = self._last_run / slug
        history_project_dir = self._history / slug

        if not current_dir.exists():
            logger.warning(
                "archive_project('%s'): current dir %s does not exist; skipping",
                name, current_dir,
            )
            return

        # Step 1 – rotate the old last_run into history
        if last_run_dir.exists():
            history_project_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            archive_dest = history_project_dir / f"run_{ts}"
            logger.info(
                "Moving last_run/%s → history/%s/run_%s", slug, slug, ts
            )
            try:
                shutil.move(str(last_run_dir), str(archive_dest))
            except Exception as move_exc:
                logger.warning("Could not move %s to %s: %s; cleaning last_run", last_run_dir, archive_dest, move_exc)
                try:
                    from scripts.trash_manager import _force_rmtree
                    _force_rmtree(last_run_dir)
                except Exception:
                    pass

        # Step 2 – move current into last_run
        logger.info("Moving current/%s → last_run/%s", slug, slug)
        last_run_dir.parent.mkdir(parents=True, exist_ok=True)
        if last_run_dir.exists():
            _force_rmtree(last_run_dir)
        for attempt in range(5):
            try:
                if last_run_dir.exists():
                    _force_rmtree(last_run_dir)
                shutil.move(str(current_dir), str(last_run_dir))
                break
            except Exception:
                if last_run_dir.exists():
                    _force_rmtree(last_run_dir)
                if attempt == 4:
                    raise
                time.sleep(0.2)
        logger.info("Archive complete for '%s'", name)

    def list_all(self) -> dict[str, dict[str, Any]]:
        """
        Return a nested summary of all known projects across all tiers.

        Returns
        -------
        dict
            ``{ project_slug: { "current": bool, "last_run": bool,
                                "history_runs": [str, ...] } }``
        """
        slugs: set[str] = set()
        for tier_dir in (self._current, self._last_run, self._history):
            if tier_dir.exists():
                slugs.update(d.name for d in tier_dir.iterdir() if d.is_dir())

        result: dict[str, dict[str, Any]] = {}
        for slug in sorted(slugs):
            history_runs: list[str] = []
            history_slug_dir = self._history / slug
            if history_slug_dir.exists():
                history_runs = sorted(
                    d.name
                    for d in history_slug_dir.iterdir()
                    if d.is_dir() and d.name.startswith("run_")
                )
            result[slug] = {
                "current": (self._current / slug).exists(),
                "last_run": (self._last_run / slug).exists(),
                "history_runs": history_runs,
            }
        return result

    def current_path(self, name: str) -> Path:
        """Return the ``current/<slug>/`` path for *name* (may not exist)."""
        return self._project_dir(name, "current")

    def last_run_path(self, name: str) -> Path:
        """Return the ``last_run/<slug>/`` path for *name* (may not exist)."""
        return self._project_dir(name, "last_run")


def _remove_tree_force(target_dir: Path) -> bool:
    """Robustly remove directory tree on Windows handling read-only git/db files."""
    import stat

    def _on_exc(func, path, exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass

    try:
        try:
            shutil.rmtree(target_dir, onexc=_on_exc)
        except TypeError:
            shutil.rmtree(target_dir, onerror=lambda f, p, e: _on_exc(f, p, e[1]))
        return not target_dir.exists()
    except Exception:
        return False


def purge_test_artifacts(engine_root: Path | str) -> list[str]:
    """
    Permanently delete phase5_e2e_test and lingering mock_* directories from
    current/, last_run/, history/, and root mock_workspace/.
    """
    engine_p = Path(engine_root).resolve()
    purged: list[str] = []

    # 1. Clean from tiers
    for tier in ("current", "last_run", "history"):
        tier_dir = engine_p / tier
        if not tier_dir.exists():
            continue
        for child in tier_dir.iterdir():
            if child.is_dir() and ("phase5_e2e_test" in child.name or child.name.startswith("mock_")):
                if _remove_tree_force(child):
                    purged.append(str(child))
                    logger.info("Purged test artifact: %s", child)

    # 2. Clean root mock_workspace
    mock_ws = engine_p / "mock_workspace"
    if mock_ws.exists():
        if _remove_tree_force(mock_ws):
            purged.append(str(mock_ws))
            logger.info("Purged mock_workspace directory: %s", mock_ws)

    return purged


def rollback_burst(
    engine_root: Path | str,
    project_name: str,
    event_id: int,
) -> dict[str, Any]:
    """
    Rollback and discard an atomic prompt burst:
    1. Retrieve event details from session.db.
    2. Rollback associated Shadow Git micro-commit (e.g. git reset --hard <commit>~1).
    3. Move the event row, patch rows, and .patch file into trash/ under scope 'selective_bursts' via TrashManager.
    4. Clean up any associated GIT event referencing this burst.
    5. Return operation status.
    """
    slug = _slug(project_name)
    engine_p = Path(engine_root).resolve()

    # Find project directory across current and last_run
    proj_dir: Path | None = None
    for tier in ("current", "last_run"):
        cand = engine_p / tier / slug
        if cand.exists() and (cand / "session.db").exists():
            proj_dir = cand
            break

    if proj_dir is None:
        return {"success": False, "message": f"Project '{slug}' not found or session.db missing."}

    db_path = proj_dir / "session.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    ev_row = cur.fetchone()
    if not ev_row:
        conn.close()
        return {"success": False, "message": f"Burst event #{event_id} not found in session.db."}

    # 1. Shadow Git micro-commit rollback
    meta = read_project_meta(engine_p, slug)
    target_path = meta.get("target_path", "")
    rolled_back_git = False
    shadow_dir = proj_dir / "shadow_git"

    if shadow_dir.exists() and target_path:
        try:
            try:
                from spd_analysis_engine.scripts.git_shadow import ShadowGit
            except (ImportError, ModuleNotFoundError):
                from scripts.git_shadow import ShadowGit
            sg = ShadowGit(shadow_dir=shadow_dir, target_dir=target_path)
            rolled_back_git = sg.rollback_burst_commit(event_id)
        except Exception as exc:
            logger.warning("ShadowGit rollback failed for event #%d: %s", event_id, exc)

    # Check for associated GIT micro-commit event in session.db
    cur.execute(
        "SELECT id FROM events WHERE event_type = 'GIT' AND summary LIKE ? ORDER BY id DESC LIMIT 1",
        (f"%Burst #{event_id}%",),
    )
    git_ev_row = cur.fetchone()
    burst_ids_to_trash = [event_id]
    if git_ev_row:
        burst_ids_to_trash.append(git_ev_row["id"])
    conn.close()

    # 2. Use TrashManager to move event + patches into trash/ under selective_bursts
    try:
        try:
            from spd_analysis_engine.scripts.trash_manager import TrashManager, SCOPE_SELECTIVE_BURSTS
        except (ImportError, ModuleNotFoundError):
            from scripts.trash_manager import TrashManager, SCOPE_SELECTIVE_BURSTS
        tm = TrashManager(engine_p)
        trash_res = tm.move_to_trash(slug, SCOPE_SELECTIVE_BURSTS, burst_ids=burst_ids_to_trash)
    except Exception as exc:
        logger.error("TrashManager error during rollback of event #%d: %s", event_id, exc)
        trash_res = {"success": False, "error": str(exc)}

    logger.info("Burst #%d rolled back successfully for project %s (git=%s)", event_id, slug, rolled_back_git)

    # 3. Clean up SQLite, remove any remaining .patch file, and prune empty inactive sessions
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM patches WHERE event_id = ?", (event_id,))
        cur.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()

        session_id = ev_row["session_id"] if "session_id" in ev_row.keys() else None
        if session_id:
            cur.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND (event_type = 'EDIT' OR event_type IS NULL OR event_type = '')",
                (session_id,),
            )
            count_row = cur.fetchone()
            rem_bursts = count_row[0] if count_row else 0

            cur.execute("SELECT status FROM sessions WHERE id = ?", (session_id,))
            s_row = cur.fetchone()
            is_active = (s_row and s_row[0] == "ACTIVE")
            if rem_bursts == 0 and not is_active:
                cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                conn.commit()
                logger.info("Pruned empty session #%d after rollback", session_id)
        conn.close()
    except Exception as exc:
        logger.warning("Error finalizing rollback DB state for event #%d: %s", event_id, exc)

    # Clean up local patch file if still present
    for patches_tier in ("current", "last_run"):
        pf = engine_p / patches_tier / slug / "patches" / f"event_{event_id}.patch"
        if pf.exists():
            try:
                pf.unlink()
            except Exception:
                pass

    return {
        "success": True,
        "event_id": event_id,
        "project": slug,
        "rolled_back_git": rolled_back_git,
        "trash_res": trash_res,
        "message": f"Burst #{event_id} successfully rolled back and moved to Trash.",
    }
```

---

### [13/27] File: `scripts\watcher_fs.py`
- **Lines:** 203 | **Size:** 6.11 KB | **Type:** py

```python
"""
scripts/watcher_fs.py
=====================
Non-blocking file-system observer using watchdog for the SPD Analysis Engine.

Classes
-------
ProjectWatcher
    Monitors a target project directory recursively, filters out noise
    (temporary files, git metadata, internal logs), and emits clean file
    mutation events into an ``EventBundler``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from .event_bundler import EventBundler

logger = logging.getLogger(__name__)

#: Directories ignored by the file watcher
IGNORED_DIRS: set[str] = {
    ".git",
    ".sf",
    ".sfdx",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "patches",
    "baseline",
    ".mypy_cache",
    ".pytest_cache",
}

#: File extensions ignored by the file watcher
IGNORED_EXTENSIONS: set[str] = {
    ".tmp",
    ".swp",
    ".swo",
    ".pyc",
    ".pyo",
    ".bak",
    ".log",
    ".db",
    ".db-shm",
    ".db-wal",
    ".pid",
}

#: Exact filenames ignored by the file watcher
IGNORED_FILES: set[str] = {
    "session.db",
    "session.db-shm",
    "session.db-wal",
    "worker.log",
    "worker.pid",
    "worker.pid.stale",
    "worker.status.json",
    "project.meta",
}


class _WatchdogEventHandler(FileSystemEventHandler):
    """
    Internal Watchdog event handler that filters ignored paths and sends
    clean file events to the EventBundler.
    """

    def __init__(self, target_path: Path, bundler: EventBundler) -> None:
        super().__init__()
        self.target_path = target_path.resolve()
        self.bundler = bundler

    def _is_ignored(self, path: Path) -> bool:
        """Check if *path* matches any ignore rule."""
        parts = path.parts
        for part in parts:
            if part in IGNORED_DIRS or part.startswith(".sf"):
                return True
        name = path.name
        if name in IGNORED_FILES:
            return True
        if name.startswith("catalog.json") or name.endswith(".__staging__") or name.endswith(".tmp"):
            return True
        if path.suffix.lower() in IGNORED_EXTENSIONS:
            return True
        return False

    def _get_rel_path(self, abs_path_str: str) -> str | None:
        """Convert system absolute path to workspace-relative path."""
        p = Path(abs_path_str).resolve()
        if self._is_ignored(p):
            return None
        try:
            rel = p.relative_to(self.target_path)
            return str(rel).replace("\\", "/")
        except ValueError:
            return None

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        rel = self._get_rel_path(event.src_path)
        if rel:
            logger.debug("Watchdog on_modified: %s", rel)
            self.bundler.add_event("modified", rel)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        rel = self._get_rel_path(event.src_path)
        if rel:
            logger.debug("Watchdog on_created: %s", rel)
            self.bundler.add_event("created", rel)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        rel = self._get_rel_path(event.src_path)
        if rel:
            logger.debug("Watchdog on_deleted: %s", rel)
            self.bundler.add_event("deleted", rel)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_rel = self._get_rel_path(event.src_path)
        dest_rel = self._get_rel_path(event.dest_path) if hasattr(event, "dest_path") else None
        if src_rel:
            self.bundler.add_event("deleted", src_rel)
        if dest_rel:
            self.bundler.add_event("created", dest_rel)


class ProjectWatcher:
    """
    Non-blocking project directory watcher.

    Wraps a ``watchdog.observers.Observer`` running in a background daemon
    thread, feeding debounced events into ``EventBundler``.

    Parameters
    ----------
    target_path : Path | str
        The root project directory being monitored.
    bundler : EventBundler
        Sliding-window event bundler receiving clean notifications.
    recursive : bool, optional
        Whether to monitor child subdirectories (default: True).
    """

    def __init__(
        self,
        target_path: Path | str,
        bundler: EventBundler,
        recursive: bool = True,
    ) -> None:
        self.target_path = Path(target_path).resolve()
        self.bundler = bundler
        self.recursive = recursive

        self._handler = _WatchdogEventHandler(self.target_path, self.bundler)
        self._observer: Observer | None = None

    def start(self) -> None:
        """Start the background watchdog observer thread."""
        if self._observer is not None and self._observer.is_alive():
            logger.debug("ProjectWatcher observer already running on %s", self.target_path)
            return

        if not self.target_path.exists():
            raise FileNotFoundError(f"Target directory does not exist: {self.target_path}")

        self._observer = Observer()
        self._observer.daemon = True
        self._observer.schedule(self._handler, str(self.target_path), recursive=self.recursive)
        self._observer.start()
        logger.info("ProjectWatcher started observing %s (recursive=%s)", self.target_path, self.recursive)

    def stop(self, timeout: float = 3.0) -> None:
        """Stop and join the observer thread."""
        if self._observer is not None:
            logger.info("Stopping ProjectWatcher on %s", self.target_path)
            self._observer.stop()
            try:
                self._observer.join(timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error joining observer thread: %s", exc)
            self._observer = None

    def is_alive(self) -> bool:
        """Return True if the observer thread is active."""
        return self._observer is not None and self._observer.is_alive()
```

---

### [14/27] File: `scripts\watcher_proc.py`
- **Lines:** 272 | **Size:** 9.26 KB | **Type:** py

```python
"""
scripts/watcher_proc.py
=======================
Low-overhead process handle inspector using psutil for the SPD Analysis Engine.

Tracks files actively opened/read by developer IDEs and runtimes (Cursor, Code,
Antigravity, Python, Node) within the monitored workspace path to record what
code files the AI analyzed prior to generating code edits.

Features
--------
1. Low-overhead background thread running periodic (1.0s) handle scans.
2. Filters processes targeting developer IDEs and language runtimes.
3. Automatically excludes internal engine files, baselines, and VCS metadata.
4. Deduplicates repeated handle reads to avoid event flooding.
5. Emits structured READ event callbacks and maintains recent reads buffer.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psutil

try:
    from scripts.ide_profiles import is_process_whitelisted
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ide_profiles import is_process_whitelisted
    except (ImportError, ModuleNotFoundError):
        def is_process_whitelisted(proc: Any, profile_name: str = "antigravity", custom_marker: str | None = None) -> bool:
            return True

logger = logging.getLogger(__name__)

#: Target developer processes to inspect for active file handles
TARGET_PROCESS_NAMES: set[str] = {
    "cursor.exe", "cursor",
    "code.exe", "code",
    "antigravity.exe", "antigravity",
    "python.exe", "python", "python3.exe", "python3", "py.exe",
    "node.exe", "node",
    "windsurf.exe", "windsurf",
    "devenv.exe",
}

#: Directories ignored during process handle scanning
IGNORED_DIRS: set[str] = {
    ".git",
    ".sf",
    ".sfdx",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "patches",
    "baseline",
    "shadow_git",
    ".mypy_cache",
    ".pytest_cache",
}

#: File extensions ignored during handle inspection
IGNORED_EXTENSIONS: set[str] = {
    ".tmp", ".swp", ".swo", ".pyc", ".pyo", ".bak", ".log",
    ".db", ".db-shm", ".db-wal", ".pid",
}

#: Substring patterns of internal IDE tasks and extensions to filter out from handle inspection
IGNORED_HANDLE_PATTERNS: tuple[str, ...] = (
    "shellintegration.ps1",
    "jsonservermain",
    "eslintserver",
    "eslintserver.js",
    "typescript-language-features",
    "apex-language-server",
    "git-credential-",
    "credential-manager",
    "git-credential-manager",
    "appdata/local/programs/microsoft vs code",
    "resources/app/extensions",
    "resources/app/out/vs",
    ".vscode/extensions",
    ".cursor/extensions",
    ".windsurf/extensions",
)



def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProcessInspector:
    """
    Monitors active file read handles of IDE processes within a target directory.

    Parameters
    ----------
    target_path : Path | str
        Root workspace directory being monitored.
    on_file_read : Callable[[dict[str, Any]], None] | None
        Callback triggered whenever a new file read is detected.
    poll_interval : float
        Interval in seconds between process inspections (default: 1.0s).
    """

    def __init__(
        self,
        target_path: Path | str,
        on_file_read: Callable[[dict[str, Any]], None] | None = None,
        poll_interval: float = 1.0,
        scan_interval: float | None = None,
        ide_profile: str = "antigravity",
        custom_marker: str | None = None,
    ) -> None:
        self.target_path = Path(target_path).resolve()
        self.on_file_read = on_file_read
        self.poll_interval = scan_interval if scan_interval is not None else poll_interval
        self.ide_profile = ide_profile
        self.custom_marker = custom_marker

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Track (pid, rel_path) tuples seen recently to avoid duplicates
        self._seen_handles: set[tuple[int, str]] = set()
        self._recent_reads: list[dict[str, Any]] = []

    def _is_ignored(self, path: Path) -> bool:
        """Check if *path* matches any ignore rule."""
        for part in path.parts:
            if part in IGNORED_DIRS or part.startswith(".sf"):
                return True
        name = path.name
        if name.startswith("catalog.json") or name.endswith(".__staging__") or name.endswith(".tmp"):
            return True
        if path.suffix.lower() in IGNORED_EXTENSIONS:
            return True
        if name in ("session.db", "worker.log", "worker.pid", "project.meta"):
            return True
        path_str = str(path).lower().replace("\\", "/")
        for pattern in IGNORED_HANDLE_PATTERNS:
            if pattern in path_str:
                return True
        return False

    def _get_rel_path(self, file_path_str: str) -> str | None:
        """Return relative POSIX path if within target_path and not ignored."""
        try:
            p = Path(file_path_str).resolve()
            if self._is_ignored(p):
                return None
            rel = p.relative_to(self.target_path)
            return str(rel).replace("\\", "/")
        except (ValueError, OSError):
            return None

    def _inspect_once(self) -> None:
        """Scan active processes and inspect their open file handles."""
        my_pid = os.getpid()

        for proc in psutil.process_iter(["pid", "name"]):
            if self._stop_event.is_set():
                break

            try:
                name = proc.info.get("name") or ""
                pid = proc.info.get("pid")
                if not pid or pid == my_pid:
                    continue

                if name.lower() not in TARGET_PROCESS_NAMES:
                    continue

                if not is_process_whitelisted(proc, self.ide_profile, self.custom_marker):
                    continue

                try:
                    cmdline = proc.cmdline()
                    cmd_str = " ".join(cmdline).lower()
                    if any(p in cmd_str for p in IGNORED_HANDLE_PATTERNS):
                        continue
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    pass

                open_files = proc.open_files()
                if not open_files:
                    continue

                for f in open_files:
                    rel_path = self._get_rel_path(f.path)
                    if not rel_path:
                        continue

                    handle_key = (pid, rel_path)
                    with self._lock:
                        if handle_key in self._seen_handles:
                            continue

                        # Cap set size to avoid memory growth
                        if len(self._seen_handles) > 3000:
                            self._seen_handles.clear()

                        self._seen_handles.add(handle_key)

                        read_record = {
                            "event_type": "READ",
                            "file_path": rel_path,
                            "process_name": name,
                            "pid": pid,
                            "timestamp": _utcnow_iso(),
                        }
                        self._recent_reads.append(read_record)

                        logger.debug(
                            "Detected file read by %s (PID %s): %s",
                            name, pid, rel_path,
                        )

                        if self.on_file_read:
                            try:
                                self.on_file_read(read_record)
                            except Exception as cb_exc:
                                logger.debug("Error in on_file_read callback: %s", cb_exc)

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
            except Exception as exc:
                logger.debug("Unexpected error during process inspect: %s", exc)
                continue

    def _run_loop(self) -> None:
        """Background scanning loop."""
        logger.info("ProcessInspector started observing %s (interval=%.1fs)", self.target_path, self.poll_interval)
        while not self._stop_event.is_set():
            self._inspect_once()
            self._stop_event.wait(self.poll_interval)

    def start(self) -> None:
        """Start the background inspector thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop and join the inspector thread."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("ProcessInspector stopped on %s", self.target_path)

    def get_recent_reads(self, clear: bool = False) -> list[dict[str, Any]]:
        """Return buffered read records since last check."""
        with self._lock:
            records = list(self._recent_reads)
            if clear:
                self._recent_reads.clear()
            return records
```

---

### [15/27] File: `scripts\web_server.py`
- **Lines:** 876 | **Size:** 35.1 KB | **Type:** py

```python
"""
scripts/web_server.py
=====================
Zero-dependency backend HTTP server & SSE streamer for the SPD Analysis Engine.

Implements ``EngineWebServer`` using Python standard library ``http.server``
and ``threading`` (no Flask or FastAPI required).

Routes
------
Static assets:
    GET /                 -> Serves web/index.html
    GET /style.css        -> Serves web/style.css
    GET /app.js           -> Serves web/app.js
    GET /*                -> Serves requested file from web/

REST API:
    GET  /api/status               -> Live worker processes, uptime, memory, CPU
    GET  /api/projects             -> All projects grouped by tier (current, last_run, history)
    POST /api/projects/start        -> Launch worker {project, path, scaffold, debounce}
    POST /api/projects/stop         -> Stop worker and trigger rotation {project}
    GET  /api/project/<name>/events -> All events & patches from session.db
    GET  /api/project/<name>/logs   -> Last 150 lines from worker.log
    GET  /api/project/<name>/export -> Full session JSON export package
    GET  /api/stream               -> Server-Sent Events (SSE) live updates (1.5s keep-alive)
"""

from __future__ import annotations

import json
import logging
import mimetypes
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from .server import (
        ProjectRoutesMixin, AIRoutesMixin, TrashRoutesMixin,
    )
    from .orchestrator import Supervisor
    from .storage_rotator import (
        StorageRotator, SessionDB, _slug,
        read_project_meta, update_project_meta, consolidate_project_history,
    )
    from .git_shadow import ShadowGit, GitCredentialManager
    from .shell_interceptor import ExecutionTracker, is_ignored_command, scrub_tokens
    from .ai_analyzer import AISynthesizer
    from .trash_manager import TrashManager
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.server import (
            ProjectRoutesMixin, AIRoutesMixin, TrashRoutesMixin,
        )
        from spd_analysis_engine.scripts.orchestrator import Supervisor
        from spd_analysis_engine.scripts.storage_rotator import (
            StorageRotator, SessionDB, _slug,
            read_project_meta, update_project_meta, consolidate_project_history,
        )
        from spd_analysis_engine.scripts.git_shadow import ShadowGit, GitCredentialManager
        from spd_analysis_engine.scripts.shell_interceptor import ExecutionTracker, is_ignored_command, scrub_tokens
        from spd_analysis_engine.scripts.ai_analyzer import AISynthesizer
        from spd_analysis_engine.scripts.trash_manager import TrashManager
    except (ImportError, ModuleNotFoundError):
        from scripts.server import (
            ProjectRoutesMixin, AIRoutesMixin, TrashRoutesMixin,
        )
        from scripts.orchestrator import Supervisor
        from scripts.storage_rotator import (
            StorageRotator, SessionDB, _slug,
            read_project_meta, update_project_meta, consolidate_project_history,
        )
        from scripts.git_shadow import ShadowGit, GitCredentialManager
        from scripts.shell_interceptor import ExecutionTracker, is_ignored_command, scrub_tokens
        from scripts.ai_analyzer import AISynthesizer
        from scripts.trash_manager import TrashManager


logger = logging.getLogger(__name__)

_NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


_utcnow_iso = _now_ist_iso


class _EngineRequestHandler(ProjectRoutesMixin, AIRoutesMixin, TrashRoutesMixin, BaseHTTPRequestHandler):
    """Custom request handler with REST API and SSE support."""

    # Disable default noisy request logging on every SSE ping
    def log_message(self, format: str, *args: Any) -> None:
        if args and str(args[0]).startswith("GET /api/stream"):
            return
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    @property
    def server_instance(self) -> EngineWebServer:
        return self.server.engine_web_server  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # HTTP Options (CORS preflight)
    # ------------------------------------------------------------------

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors_headers()
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message, "status": "error"}, status=status)

    # ------------------------------------------------------------------
    # GET Handlers
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"

        # 1. API: /api/status
        if path == "/api/status":
            self._handle_api_status()
            return

        # 2. API: /api/projects
        if path == "/api/projects":
            self._handle_api_projects()
            return

        # 3. API: /api/stream (SSE)
        if path == "/api/stream":
            self._handle_api_stream()
            return

        # 4. API: /api/project/<name>/events
        if path.startswith("/api/project/") and path.endswith("/events"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_events(proj_name)
                return

        # 5. API: /api/project/<name>/logs
        if path.startswith("/api/project/") and path.endswith("/logs"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_logs(proj_name)
                return

        # 6. API: /api/project/<name>/export
        if path.startswith("/api/project/") and path.endswith("/export"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_export(proj_name)
                return

        # 7. API: /api/project/<name>/shadow-git
        if path.startswith("/api/project/") and path.endswith("/shadow-git"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_shadow_git(proj_name)
                return

        # 8. API: /api/git/config
        if path == "/api/git/config":
            self._handle_api_get_git_config()
            return

        # 9. API: /api/ai/config
        if path == "/api/ai/config":
            self._handle_api_get_ai_config()
            return

        # 10. API: /api/project/<name>/specs
        if path.startswith("/api/project/") and path.endswith("/specs"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_project_specs(proj_name)
                return

        # 11. API: /api/project/<name>/analyze-batch/status
        if path.startswith("/api/project/") and path.endswith("/analyze-batch/status"):
            parts = path.split("/")
            if len(parts) == 6:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_analyze_batch_status(proj_name)
                return

        # 12. API: /api/trash
        if path == "/api/trash":
            self._handle_api_get_trash()
            return

        # 13. API: /api/project/<name>/sessions
        if path.startswith("/api/project/") and path.endswith("/sessions"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_sessions(proj_name)
                return

        # 14. Static file serving from web/
        self._handle_static_file(parsed.path)


    # ------------------------------------------------------------------
    # POST Handlers
    # ------------------------------------------------------------------

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Read JSON body
        content_len = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_len > 0:
            try:
                raw_body = self.rfile.read(content_len).decode("utf-8")
                body = json.loads(raw_body)
            except Exception as exc:
                self._send_error(f"Malformed JSON body: {exc}", status=400)
                return

        if path == "/api/projects/start":
            self._handle_api_start_project(body)
            return

        if path == "/api/projects/stop":
            self._handle_api_stop_project(body)
            return

        if path == "/api/git/config":
            self._handle_api_save_git_config(body)
            return

        if path == "/api/git/test":
            self._handle_api_test_git_connection(body)
            return

        if path == "/api/git/create-repo":
            self._handle_api_create_git_repo(body)
            return

        if path == "/api/ai/config":
            self._handle_api_save_ai_config(body)
            return

        if path == "/api/ai/test":
            self._handle_api_test_ai_connection(body)
            return

        # API: /api/project/<name>/prepare-sync
        if path.startswith("/api/project/") and path.endswith("/prepare-sync"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_prepare_sync(proj_name, body)
                return

        # API: /api/project/<name>/analyze-batch
        if path.startswith("/api/project/") and path.endswith("/analyze-batch"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_analyze_batch(proj_name, body)
                return

        # API: /api/project/<name>/resume
        if path.startswith("/api/project/") and path.endswith("/resume"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_resume_project(proj_name)
                return

        # API: /api/project/<name>/exec
        if path.startswith("/api/project/") and path.endswith("/exec"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_record_exec(proj_name, body)
                return

        # API: /api/project/<name>/git-push
        if path.startswith("/api/project/") and path.endswith("/git-push"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_git_push(proj_name, body)
                return

        # API: /api/project/<name>/analyze-event/<event_id>
        if path.startswith("/api/project/") and "/analyze-event/" in path:
            parts = path.split("/")
            if len(parts) == 6 and parts[4] == "analyze-event":
                proj_name = urllib.parse.unquote(parts[3])
                event_id_str = parts[5]
                self._handle_api_analyze_event(proj_name, event_id_str, body)
                return

        # API: /api/project/<name>/analyze-exec/<event_id>
        if path.startswith("/api/project/") and "/analyze-exec/" in path:
            parts = path.split("/")
            if len(parts) == 6 and parts[4] == "analyze-exec":
                proj_name = urllib.parse.unquote(parts[3])
                event_id_str = parts[5]
                self._handle_api_analyze_exec(proj_name, event_id_str, body)
        # API: /api/project/<name>/rollback-burst/<event_id>
        if path.startswith("/api/project/") and "/rollback-burst/" in path:
            parts = path.split("/")
            if len(parts) == 6 and parts[4] == "rollback-burst":
                proj_name = urllib.parse.unquote(parts[3])
                event_id_str = parts[5]
                try:
                    eid = int(event_id_str)
                    self._handle_api_rollback_burst(proj_name, eid)
                except ValueError:
                    self._send_error("Invalid event_id; must be an integer", status=400)
                return

        # API: /api/project/<name>/seal-burst
        if path.startswith("/api/project/") and path.endswith("/seal-burst"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_seal_burst(proj_name)
                return

        # API: /api/project/<name>/delete
        if path.startswith("/api/project/") and path.endswith("/delete"):
            parts = path.split("/")
            if len(parts) == 5:
                proj_name = urllib.parse.unquote(parts[3])
                self._handle_api_delete_project(proj_name, body)
                return

        # Trash REST endpoints (POST fallback)
        if path.startswith("/api/trash/restore/"):
            trash_id = urllib.parse.unquote(path[len("/api/trash/restore/"):])
            self._handle_api_restore_trash(trash_id)
            return

        if path.startswith("/api/trash/purge/"):
            trash_id = urllib.parse.unquote(path[len("/api/trash/purge/"):])
            self._handle_api_purge_trash(trash_id)
            return

        if path == "/api/trash/purge-all":
            self._handle_api_purge_all_trash()
            return

        self._send_error(f"Endpoint not found: {path}", status=404)

    # ------------------------------------------------------------------
    # DELETE Handlers
    # ------------------------------------------------------------------

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/trash/purge/"):
            trash_id = urllib.parse.unquote(path[len("/api/trash/purge/"):])
            self._handle_api_purge_trash(trash_id)
            return

        if path == "/api/trash/purge-all":
            self._handle_api_purge_all_trash()
            return

        self._send_error(f"Endpoint not found: {path}", status=404)

    # ------------------------------------------------------------------
    # SSE Stream & Static Assets (Handlers in server/ mixins)
    # ------------------------------------------------------------------

    def _handle_api_stream(self) -> None:
        """Server-Sent Events (SSE) streaming endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        engine = self.server_instance

        # Initial connect message
        try:
            init_frame = f"event: connected\ndata: {json.dumps({'time': _utcnow_iso()})}\n\n"
            self.wfile.write(init_frame.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

        while not engine._shutdown_event.is_set():
            try:
                sup = engine.supervisor
                statuses = sup.get_status()
                payload = {
                    "server_time": _utcnow_iso(),
                    "workers": statuses,
                    "active_count": sum(1 for s in statuses if s.get("alive")),
                }
                msg = f"event: ping\ndata: {json.dumps(payload)}\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
                time.sleep(1.5)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                break
            except Exception as exc:
                logger.debug("Error streaming SSE data: %s", exc)
                break

    # ------------------------------------------------------------------
    # Static Assets Serving
    # ------------------------------------------------------------------

    def _handle_static_file(self, raw_path: str) -> None:
        engine = self.server_instance
        clean_path = raw_path.lstrip("/")
        if not clean_path or clean_path == "/":
            clean_path = "index.html"

        # Prevent directory traversal attacks
        file_path = (engine.web_dir / clean_path).resolve()
        try:
            file_path.relative_to(engine.web_dir)
        except ValueError:
            self._send_error("Forbidden", status=403)
            return

        if not file_path.is_file():
            self._send_error(f"File not found: {clean_path}", status=404)
            return

        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type is None:
            mime_type = "application/octet-stream"
        if mime_type.startswith("text/") or mime_type in ("application/javascript", "application/json"):
            mime_type += "; charset=utf-8"

        try:
            content = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(content)
        except Exception as exc:
            self._send_error(f"Error reading file: {exc}", status=500)


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that silences socket disconnects and aborts on Windows."""

    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return  # Silently ignore client tab reloads/browser disconnects on Windows
        super().handle_error(request, client_address)


class EngineWebServer:
    """
    Standard-library based HTTP server for the SPD Analysis Engine.

    Parameters
    ----------
    engine_root : Path | str
        Root directory of the SPD Analysis Engine.
    host : str
        Host IP to bind to (default: ``127.0.0.1``).
    port : int
        Port to bind to (default: ``8765``).
    web_dir : Path | str | None
        Directory containing static web files (defaults to ``<engine_root>/web``).
    """

    def __init__(
        self,
        engine_root: Path | str,
        host: str = "127.0.0.1",
        port: int = 8765,
        web_dir: Path | str | None = None,
    ) -> None:
        self.engine_root = Path(engine_root).resolve()
        self.host = host
        self.port = port
        self.web_dir = Path(web_dir).resolve() if web_dir else (self.engine_root / "web")

        self.supervisor = Supervisor(self.engine_root)
        self.rotator = StorageRotator(self.engine_root)
        self.trash_mgr = TrashManager(self.engine_root)
        self._batch_jobs: dict[str, dict[str, Any]] = {}
        self._batch_jobs_lock = threading.Lock()

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()

    def start(self, background: bool = True) -> int:
        """
        Start the HTTP server. If *port* is taken, tries successive ports.
        Returns the bound port.
        """
        self._shutdown_event.clear()

        attempts = 10
        current_port = self.port
        while attempts > 0:
            try:
                self._server = _QuietThreadingHTTPServer((self.host, current_port), _EngineRequestHandler)
                self._server.engine_web_server = self  # type: ignore[attr-defined]
                self.port = self._server.server_address[1]
                break
            except OSError as exc:
                logger.warning("Port %d busy: %s; trying %d...", current_port, exc, current_port + 1)
                current_port += 1
                attempts -= 1

        if self._server is None:
            raise RuntimeError(f"Could not bind to any port near {self.port}")

        logger.info("EngineWebServer listening on http://%s:%d (web_dir=%s)", self.host, self.port, self.web_dir)

        if background:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        else:
            self._server.serve_forever()

        return self.port

    def shutdown(self) -> None:
        """Gracefully stop the HTTP server and all background connections."""
        self._shutdown_event.set()
        if self._server is not None:
            logger.info("Stopping EngineWebServer...")
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("EngineWebServer stopped.")

    def _run_async_batch(
        self,
        slug: str,
        job_id: str,
        event_ids: list[int],
        db_path: Path,
        target_path: Path | None,
    ) -> None:
        """Background worker executing batch AI synthesis sequentially with deep context."""
        logger.info("Starting async batch AI analysis for %s (job %s, %d events)", slug, job_id, len(event_ids))
        try:
            syn = AISynthesizer(self.engine_root)
            db = SessionDB(db_path)
            conn = db.conn
            assert conn is not None
            cur = conn.cursor()

            previous_intent = None
            analyzed = 0

            for idx, eid in enumerate(event_ids, 1):
                # Check if job was superseded
                with self._batch_jobs_lock:
                    curr_job = self._batch_jobs.get(slug, {})
                    if curr_job.get("job_id") != job_id:
                        logger.info("Batch job %s for %s was superseded. Halting.", job_id, slug)
                        db.close()
                        return
                    self._batch_jobs[slug]["current_event_id"] = eid
                    self._batch_jobs[slug]["current_index"] = idx

                cur.execute("SELECT * FROM events WHERE id = ?", (eid,))
                erow = cur.fetchone()
                if not erow:
                    continue
                event_dict = dict(erow)

                cur.execute("SELECT file_path, diff_content FROM patches WHERE event_id = ?", (eid,))
                prows = cur.fetchall()
                patches = [dict(p) for p in prows]

                # Check rate limiter only for live external Gemini calls
                cfg = syn.load_config(syn.engine_root)
                pref = (cfg.get("preferred_provider") or "gemini").lower()
                if pref == "gemini":
                    def _on_cooldown(remaining: float) -> None:
                        with self._batch_jobs_lock:
                            if self._batch_jobs.get(slug, {}).get("job_id") == job_id:
                                self._batch_jobs[slug]["cooling_down"] = True
                                self._batch_jobs[slug]["cooldown_remaining_s"] = round(remaining, 1)

                    syn.rate_limiter.wait_if_needed(progress_callback=_on_cooldown)

                    with self._batch_jobs_lock:
                        if self._batch_jobs.get(slug, {}).get("job_id") == job_id:
                            self._batch_jobs[slug]["cooling_down"] = False
                            self._batch_jobs[slug]["cooldown_remaining_s"] = 0.0

                try:
                    analysis = syn.analyze_burst(
                        project_name=slug,
                        workspace_path=str(target_path) if target_path else "",
                        event=event_dict,
                        patches=patches,
                        previous_summary=previous_intent,
                    )
                    previous_intent = analysis.get("intent", "")
                    db.update_event_ai_summary(eid, json.dumps(analysis))
                    analyzed += 1
                except Exception as inner_exc:
                    logger.warning("Failed analyzing event #%d for %s: %s", eid, slug, inner_exc)
                    db.update_event_ai_summary(eid, json.dumps({
                        "intent": f"Burst #{eid} - Analysis pending/failed",
                        "architecture_impact": str(inner_exc),
                        "key_modifications": [],
                        "functionality_gained": "N/A",
                        "summary": [str(inner_exc)],
                    }))
                    analyzed += 1

                with self._batch_jobs_lock:
                    if self._batch_jobs.get(slug, {}).get("job_id") == job_id:
                        self._batch_jobs[slug]["analyzed_count"] = analyzed

            db.close()
            with self._batch_jobs_lock:
                if self._batch_jobs.get(slug, {}).get("job_id") == job_id:
                    self._batch_jobs[slug]["status"] = "completed"
                    self._batch_jobs[slug]["completed_at"] = _now_ist_iso()
            logger.info("Completed async batch AI analysis for %s (job %s)", slug, job_id)
        except Exception as exc:
            logger.exception("Fatal error in async batch job %s for %s", job_id, slug)
            with self._batch_jobs_lock:
                if self._batch_jobs.get(slug, {}).get("job_id") == job_id:
                    self._batch_jobs[slug]["status"] = "failed"
                    self._batch_jobs[slug]["error"] = str(exc)

    # ------------------------------------------------------------------
    # Internal SQLite query helpers
    # ------------------------------------------------------------------

    def _count_events_in_db(self, db_path: Path) -> int:
        """Count prompt burst events (EDIT events) recorded in the database."""
        if not db_path.exists():
            return 0
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM events WHERE event_type = 'EDIT' OR event_type IS NULL OR event_type = ''")
            row = cur.fetchone()
            count = row[0] if row else 0
            conn.close()
            return count
        except Exception:
            return 0

    def _read_sessions_from_db(self, db_path: Path) -> list[dict[str, Any]]:
        """Read all sessions from session.db with burst counts and active state."""
        if not db_path.exists():
            return []
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT id, project_name, target_path, start_time, end_time, status FROM sessions ORDER BY id ASC")
            s_rows = cur.fetchall()

            cur.execute("SELECT session_id, COUNT(*) as c FROM events WHERE event_type = 'EDIT' GROUP BY session_id")
            burst_counts = {r["session_id"]: r["c"] for r in cur.fetchall()}
            conn.close()

            sessions = []
            for r in s_rows:
                sid = r["id"]
                is_active = (r["status"] == "ACTIVE")
                b_count = burst_counts.get(sid, 0)
                if b_count == 0 and not is_active:
                    continue
                sessions.append({
                    "id": sid,
                    "session_id": sid,
                    "project_name": r["project_name"],
                    "target_path": r["target_path"],
                    "start_time": r["start_time"],
                    "end_time": r["end_time"],
                    "status": r["status"],
                    "is_active": is_active,
                    "burst_count": b_count,
                    "bursts_count": b_count,
                    "label": f"Session {sid} ({'Active' if is_active else str(b_count) + ' bursts'})",
                })
            return sessions
        except Exception as exc:
            logger.debug("Error reading sessions from %s: %s", db_path, exc)
            return []

    def _read_events_from_db(self, db_path: Path) -> list[dict[str, Any]]:
        """Read all events and their linked patches from session.db with clean sequential burst indices."""
        if not db_path.exists():
            return []

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("PRAGMA table_info(events)")
        cols = [r["name"] for r in cur.fetchall()]
        has_ai_summary = "ai_summary" in cols
        has_executor = "executor" in cols

        select_cols = ["id", "session_id", "event_type", "timestamp", "first_file_touched", "summary"]
        if has_ai_summary:
            select_cols.append("ai_summary")
        if has_executor:
            select_cols.append("executor")
        cur.execute(f"SELECT {', '.join(select_cols)} FROM events ORDER BY id ASC")
        event_rows = cur.fetchall()

        events: list[dict[str, Any]] = []
        burst_by_session: dict[int, int] = {}

        for er in event_rows:
            raw_first_file = er["first_file_touched"] or ""
            raw_summary = er["summary"] or ""

            # 1. Filter out internal IDE EXEC noise rows
            if er["event_type"] == "EXEC":
                if is_ignored_command(raw_first_file) or is_ignored_command(raw_summary):
                    continue

            eid = er["id"]
            sid = er["session_id"]
            first_file_scrubbed = scrub_tokens(raw_first_file) if raw_first_file else None
            summary_scrubbed = scrub_tokens(raw_summary) if raw_summary else None
            executor_val = er["executor"] if has_executor else None

            cur.execute(
                "SELECT id, file_path, diff_content FROM patches WHERE event_id = ? ORDER BY id ASC",
                (eid,),
            )
            patch_rows = cur.fetchall()

            patches = []
            total_adds = 0
            total_dels = 0
            for pr in patch_rows:
                diff_text = pr["diff_content"] or ""
                adds = 0
                dels = 0
                for line in diff_text.splitlines():
                    if line.startswith("+") and not line.startswith("+++"):
                        adds += 1
                    elif line.startswith("-") and not line.startswith("---"):
                        dels += 1
                total_adds += adds
                total_dels += dels
                patches.append({
                    "id": pr["id"],
                    "file_path": pr["file_path"],
                    "diff_content": pr["diff_content"],
                    "additions": adds,
                    "deletions": dels,
                    "lines_added": adds,
                    "lines_deleted": dels,
                })

            # 2. Sequential burst numbering per session
            is_burst = (er["event_type"] == "EDIT") or (len(patches) > 0)
            if is_burst:
                burst_by_session[sid] = burst_by_session.get(sid, 0) + 1
                burst_num = burst_by_session[sid]
                session_burst_label = f"Session {sid} • Burst #{burst_num}"
            else:
                burst_num = None
                session_burst_label = f"Session {sid} • {er['event_type']}"

            ai_summary_parsed = None
            if has_ai_summary and er["ai_summary"]:
                try:
                    ai_summary_parsed = json.loads(er["ai_summary"])
                except Exception:
                    ai_summary_parsed = {
                        "intent": str(er["ai_summary"]),
                        "summary": [],
                        "functionality_gained": "",
                    }

            events.append(
                {
                    "id": eid,
                    "session_id": sid,
                    "burst_num": burst_num,
                    "session_burst_label": session_burst_label,
                    "event_type": er["event_type"],
                    "timestamp": er["timestamp"],
                    "first_file_touched": first_file_scrubbed,
                    "summary": summary_scrubbed,
                    "executor": executor_val,
                    "ai_summary": ai_summary_parsed,
                    "patches_count": len(patches),
                    "patches": patches,
                    "additions": total_adds,
                    "deletions": total_dels,
                }
            )

        conn.close()
        return events



if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SPD Analysis Engine Web Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host IP to bind to")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    args = parser.parse_args()

    engine_root = Path(__file__).resolve().parent.parent
    server = EngineWebServer(engine_root, host=args.host, port=args.port)
    actual_port = server.start(background=True)
    print(f"EngineWebServer listening on http://{args.host}:{actual_port}")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        server.shutdown()
```

---

### [16/27] File: `scripts\ai\__init__.py`
- **Lines:** 33 | **Size:** 0.79 KB | **Type:** py

```python
"""
scripts/ai package
==================
Modular AI intelligence layer for the SPD Analysis Engine.
"""

from .rate_limiter import RateLimitManager
from .cache_manager import AICacheManager
from .prompt_builder import (
    is_sanitized_patch,
    detect_tech_stack,
    generate_directory_tree,
    chunk_diff,
    build_burst_prompt,
    build_command_prompt,
)
from .provider_gemini import call_gemini, test_gemini_connection
from .provider_ollama import call_ollama, test_ollama_connection

__all__ = [
    "RateLimitManager",
    "AICacheManager",
    "is_sanitized_patch",
    "detect_tech_stack",
    "generate_directory_tree",
    "chunk_diff",
    "build_burst_prompt",
    "build_command_prompt",
    "call_gemini",
    "test_gemini_connection",
    "call_ollama",
    "test_ollama_connection",
]
```

---

### [17/27] File: `scripts\ai\cache_manager.py`
- **Lines:** 153 | **Size:** 5.49 KB | **Type:** py

```python
"""
scripts/ai/cache_manager.py
===========================
Hierarchical Categorical AI Cache Manager for the SPD Analysis Engine.

Restructures ai_data/cache/ into a project- and session-scoped tree:
  ai_data/cache/<project_slug>/session_<N>/burst_<B>_<diff_hash[:16]>.json
  ai_data/cache/<project_slug>/commands/cmd_<hash[:16]>.json

Maintains full backward-compatible fallback lookup in flat ai_data/cache/
for older legacy cache files.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _to_slug(name: str) -> str:
    """Normalize project name into directory slug."""
    try:
        from spd_analysis_engine.scripts.storage_rotator import _slug
        return _slug(name)
    except (ImportError, ModuleNotFoundError):
        try:
            from scripts.storage_rotator import _slug
            return _slug(name)
        except Exception:
            import re
            s = str(name).strip().lower()
            s = re.sub(r"[^\w\s-]", "", s)
            return re.sub(r"[-\s]+", "_", s)


class AICacheManager:
    """
    Manages hierarchical disk caching of diff and command AI analyses.
    """

    def __init__(self, cache_dir: Path | str) -> None:
        self.cache_dir = Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_paths(
        self,
        diff_hash: str,
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> tuple[Path, Path]:
        """
        Compute (hierarchical_path, flat_legacy_path) for an analysis payload.
        """
        flat_path = self.cache_dir / f"{diff_hash}.json"
        if not project_name:
            return flat_path, flat_path

        slug = _to_slug(project_name)

        if diff_hash.startswith("cmd_"):
            clean_hash = diff_hash[4:20] if len(diff_hash) >= 20 else diff_hash[4:]
            hier_path = self.cache_dir / slug / "commands" / f"cmd_{clean_hash}.json"
        else:
            clean_hash = diff_hash[:16]
            b_num = burst_id or 1
            if session_id is not None:
                hier_path = self.cache_dir / slug / f"session_{session_id}" / f"burst_{b_num}_{clean_hash}.json"
            else:
                hier_path = self.cache_dir / slug / f"burst_{b_num}_{clean_hash}.json"

        return hier_path, flat_path

    def get(
        self,
        diff_hash: str,
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Retrieve cached analysis. Checks hierarchical path first, then glob,
        then falls back to flat legacy cache file.
        """
        hier_path, flat_path = self._resolve_paths(diff_hash, project_name, session_id, burst_id)

        # 1. Exact hierarchical path
        if hier_path.exists():
            try:
                data = json.loads(hier_path.read_text(encoding="utf-8"))
                data["cached"] = True
                data["diff_hash"] = diff_hash
                logger.debug("Hierarchical cache hit: %s", hier_path.name)
                return data
            except Exception as exc:
                logger.warning("Corrupted cache file %s: %s", hier_path, exc)

        # 2. Session directory glob (in case burst index differs)
        if project_name and session_id is not None and not diff_hash.startswith("cmd_"):
            slug = _to_slug(project_name)
            sess_dir = self.cache_dir / slug / f"session_{session_id}"
            if sess_dir.exists():
                clean_hash = diff_hash[:16]
                for f in sess_dir.glob(f"burst_*_{clean_hash}.json"):
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        data["cached"] = True
                        data["diff_hash"] = diff_hash
                        logger.debug("Hierarchical session glob cache hit: %s", f.name)
                        return data
                    except Exception:
                        pass

        # 3. Flat legacy cache fallback
        if flat_path.exists():
            try:
                data = json.loads(flat_path.read_text(encoding="utf-8"))
                data["cached"] = True
                data["diff_hash"] = diff_hash
                logger.debug("Legacy flat cache hit: %s", flat_path.name)
                return data
            except Exception as exc:
                logger.warning("Corrupted legacy cache file %s: %s", flat_path, exc)

        return None

    def save(
        self,
        diff_hash: str,
        analysis: dict[str, Any],
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> None:
        """
        Persist analysis into hierarchical tree and ensure parent directory exists.
        """
        hier_path, flat_path = self._resolve_paths(diff_hash, project_name, session_id, burst_id)
        try:
            hier_path.parent.mkdir(parents=True, exist_ok=True)
            hier_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
            logger.debug("Saved to hierarchical cache: %s", hier_path)
        except Exception as exc:
            logger.warning("Could not write hierarchical cache file %s: %s", hier_path, exc)
            try:
                flat_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
            except Exception:
                pass
```

---

### [18/27] File: `scripts\ai\prompt_builder.py`
- **Lines:** 297 | **Size:** 11.16 KB | **Type:** py

```python
"""
scripts/ai/prompt_builder.py
============================
Tech stack detection, directory tree visualization, diff chunking,
and structured prompt construction for AI model synthesis.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


SANITIZATION_IGNORE_PATTERNS: tuple[str, ...] = (
    ".sf/", ".sf\\", "/.sf/", "\\.sf\\",
    ".sfdx/", ".sfdx\\", "/.sfdx/", "\\.sfdx\\",
    "catalog.json",
    ".__staging__",
    ".tmp",
    "node_modules/", "node_modules\\",
    "__pycache__/", "__pycache__\\",
    ".git/", ".git\\",
)


def is_sanitized_patch(file_path: str) -> bool:
    """Return True if patch file is clean project source code, False if noise/temporary artifact."""
    if not file_path:
        return False
    fp = file_path.lower().replace("\\", "/")
    if fp.startswith(".sf/") or fp.startswith(".sfdx/") or fp.startswith("node_modules/") or fp.startswith("__pycache__/") or fp.startswith(".git/"):
        return False
    if any(seg in fp for seg in ("/.sf/", "/.sfdx/", "/node_modules/", "/__pycache__/", "/.git/")):
        return False
    if "catalog.json" in fp or ".__staging__" in fp or fp.endswith(".tmp"):
        return False
    return True


def detect_tech_stack(workspace_path: Path | str | None) -> str:
    """Detect workspace tech stack from project indicator files."""
    if not workspace_path:
        return "Generic Software Codebase"
    wp = Path(workspace_path)
    if not wp.exists() or not wp.is_dir():
        return "Generic Software Codebase"

    indicators: list[str] = []

    # JavaScript / Node / Web
    pkg_json = wp / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "react" in deps:
                indicators.append("React")
            elif "vue" in deps:
                indicators.append("Vue")
            elif "svelte" in deps:
                indicators.append("Svelte")
            elif "next" in deps:
                indicators.append("Next.js")
            if "vite" in deps:
                indicators.append("Vite")
            if "typescript" in deps:
                indicators.append("TypeScript")
            elif not indicators:
                indicators.append("Node.js")
        except Exception:
            indicators.append("Node.js")
    elif any(wp.glob("*.html")) or any((wp / "web").glob("*.html") if (wp / "web").exists() else ()):
        indicators.append("Vanilla HTML5/CSS3/JavaScript")

    # Python
    py_indicators = [wp / "requirements.txt", wp / "pyproject.toml", wp / "setup.py"]
    if any(p.exists() for p in py_indicators) or any(wp.glob("*.py")) or (wp / "scripts").exists():
        indicators.append("Python")

    # Rust, Go, Java, Salesforce
    if (wp / "Cargo.toml").exists():
        indicators.append("Rust")
    if (wp / "go.mod").exists():
        indicators.append("Go")
    if (wp / "pom.xml").exists() or (wp / "build.gradle").exists():
        indicators.append("Java")
    if (wp / "sfdx-project.json").exists() or (wp / ".sf").exists():
        indicators.append("Salesforce DX / Apex")

    if not indicators:
        return "Polyglot Software Project"
    return " + ".join(indicators)


def generate_directory_tree(workspace_path: Path | str | None, max_depth: int = 2) -> str:
    """Generate lightweight 2-level directory tree of the workspace, excluding ignore patterns."""
    if not workspace_path:
        return "(Workspace directory tree not available)"
    wp = Path(workspace_path)
    if not wp.exists() or not wp.is_dir():
        return f"{wp.name}/ (Path not found)"

    ignore_set = {
        ".git", ".sf", ".sfdx", "node_modules", "__pycache__", ".venv", "venv",
        ".idea", ".vscode", "trash", "ai_data", ".pytest_cache", ".mypy_cache",
        "current", "last_run", "history", "dist", "build"
    }

    tree_lines = [f"{wp.name}/"]

    def _walk(curr: Path, depth: int, prefix: str) -> None:
        if depth > max_depth:
            return
        try:
            items = sorted(curr.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception:
            return

        filtered = [
            it for it in items
            if it.name not in ignore_set
            and not it.name.startswith(".__staging__")
            and not it.name.endswith(".tmp")
            and not (it.name.startswith(".") and it.is_dir())
        ]

        for i, item in enumerate(filtered[:18]):
            is_last = (i == len(filtered) - 1 or i == 17)
            connector = "└── " if is_last else "├── "
            suffix = "/" if item.is_dir() else ""
            tree_lines.append(f"{prefix}{connector}{item.name}{suffix}")
            if item.is_dir() and depth < max_depth:
                sub_prefix = prefix + ("    " if is_last else "│   ")
                _walk(item, depth + 1, sub_prefix)
        if len(filtered) > 18:
            tree_lines.append(f"{prefix}└── ... [{len(filtered) - 18} more items]")

    _walk(wp, 1, "")
    return "\n".join(tree_lines[:40])


def chunk_diff(diff_text: str, max_lines: int = 250, max_chars: int = 30000) -> str:
    """
    Intelligently chunk/truncate diffs exceeding max_lines or max_chars to prevent
    exceeding model context or wasting tokens/TPM limits.
    Preserves diff and file headers while truncating internal file diff bodies.
    """
    file_chunks = [c for c in re.split(r"(?=^diff --git |^--- )", diff_text, flags=re.MULTILINE) if c.strip()]

    if len(file_chunks) > 1 and (len(diff_text) > max_chars or len(diff_text.splitlines()) > max_lines):
        processed_chunks = []
        per_file_chars = max(1000, max_chars // len(file_chunks))
        per_file_lines = max(10, max_lines // len(file_chunks))
        for chunk in file_chunks:
            c_lines = chunk.splitlines()
            headers = [l for l in c_lines if l.startswith(('diff ', 'index ', '---', '+++', '@@'))]
            body_lines = [l for l in c_lines if not l.startswith(('diff ', 'index ', '---', '+++', '@@'))]

            if len(body_lines) > per_file_lines or len(chunk) > per_file_chars:
                keep = max(3, per_file_lines // 2)
                head_body = body_lines[:keep]
                tail_body = body_lines[-keep:] if len(body_lines) > keep * 2 else []
                omitted_lines = len(body_lines) - (len(head_body) + len(tail_body))
                new_chunk = (
                    "\n".join(headers + head_body)
                    + f"\n... [{omitted_lines} lines of internal diff omitted for analysis] ...\n"
                    + "\n".join(tail_body)
                )
                processed_chunks.append(new_chunk)
            else:
                processed_chunks.append(chunk)
        truncated = "\n".join(processed_chunks)
    else:
        lines = diff_text.splitlines()
        if len(lines) > max_lines:
            half = max_lines // 2
            head = lines[:half]
            tail = lines[-half:]
            omitted = len(lines) - (len(head) + len(tail))
            truncated = (
                "\n".join(head)
                + f"\n\n... [{omitted} lines of unified diff omitted for analysis] ...\n\n"
                + "\n".join(tail)
            )
        else:
            truncated = diff_text

    # Character budget guard: ensure total length <= max_chars
    if len(truncated) > max_chars:
        margin = 400
        head_len = (max_chars - margin) // 2
        tail_len = head_len
        truncated = (
            truncated[:head_len]
            + f"\n\n... [diff truncated from {len(truncated)} to {max_chars} chars to respect API TPM limits] ...\n\n"
            + truncated[-tail_len:]
        )

    return truncated


def build_burst_prompt(
    diff_text: str,
    first_file: str | None = None,
    summary: str | None = None,
    files: list[str] | None = None,
    project_name: str | None = None,
    workspace_path: Path | str | None = None,
    tech_stack: str | None = None,
    dir_tree: str | None = None,
    previous_summary: str | dict[str, Any] | None = None,
    executed_commands: list[str] | None = None,
) -> str:
    """Build the prompt for prompt burst diff analysis."""
    topology_parts = []
    if project_name:
        topology_parts.append(f"Project: {project_name}")
    if workspace_path:
        topology_parts.append(f"Workspace: {workspace_path}")
    if tech_stack:
        topology_parts.append(f"Tech Stack: {tech_stack}")
    topology_header = " | ".join(topology_parts) if topology_parts else "Project Topology"

    dir_tree_block = f"\nProject Directory Structure:\n`` `\n{dir_tree}\n`` `\n" if dir_tree else ""

    files_list_str = ", ".join(files) if files else (first_file or "unknown")
    commands_block = ""
    if executed_commands:
        commands_block = "\nAssociated Terminal Commands:\n" + "\n".join(f"- `{c}`" for c in executed_commands[:5]) + "\n"

    prev_block = ""
    if previous_summary:
        p_text = previous_summary if isinstance(previous_summary, str) else previous_summary.get("intent", "")
        if p_text:
            prev_block = f"\nPrevious Burst Intent: {p_text}\n"

    return f"""You are a senior principal software architect and tech lead.
Analyze this code modification burst recorded during an AI-assisted development session.

[{topology_header}]
{dir_tree_block}
Files Touched: {files_list_str}
Initial Trigger File: {first_file or 'unknown'}
Event Context: {summary or 'Multi-file code edit burst'}
{commands_block}{prev_block}

Unified Code Diffs:
`` `diff
{diff_text}
`` `

Instructions:
Respond with ONLY a valid, parseable JSON object matching this schema:
{{
  "intent": "Plain-English explanation of the developer's / AI's core objective in this burst",
  "architecture_impact": "How these modifications alter project architecture, interfaces, or contracts",
  "key_modifications": [
    {{"file": "filename", "change_summary": "Specific structural or logic change made in this file"}}
  ],
  "functionality_gained": "What users, developers, or tests can do now that they couldn't before",
  "summary": ["Bullet 1", "Bullet 2", "Bullet 3"]
}}"""


def build_command_prompt(
    project_name: str,
    clean_cmd: str,
    exit_code: int,
    duration_s: float,
    surrounding_context: str = "",
) -> str:
    """Build prompt for command-level explanation."""
    return f"""You are a senior DevOps, systems architecture, and security engineer.
Analyze the following terminal command execution recorded during a software development session.

Project: {project_name}
Command: {clean_cmd}
Exit Code: {exit_code}
Execution Duration: {duration_s:.2f} seconds
{surrounding_context}

Instructions:
Respond with ONLY a valid, parseable JSON object without markdown fences, matching this exact schema:
{{
  "intent": "Concise plain-English goal explaining why this specific command was invoked",
  "technical_purpose": "Compiler, interpreter, runtime, or CLI tool invoked, along with the purpose of key flags used",
  "outcome_analysis": "Assessment of exit code {exit_code} ({'Success' if exit_code == 0 else 'Failure'}) and execution duration ({duration_s:.2f}s)",
  "summary": [
    "Key takeaway 1",
    "Key takeaway 2"
  ]
}}"""
```

---

### [19/27] File: `scripts\ai\provider_gemini.py`
- **Lines:** 219 | **Size:** 8.55 KB | **Type:** py

```python
"""
scripts/ai/provider_gemini.py
=============================
Google Gemini API provider integration with official Google GenAI SDK,
HTTPS REST fallback, exponential backoff, and connection verification.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def call_gemini(
    prompt: str,
    gemini_cfg: dict[str, Any],
    rate_limiter: Any,
    parse_json_func: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Issue API call to Gemini using official Google GenAI SDK or HTTPS fallback with rate-limit retry."""
    api_key = gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key or "__REPLACE_" in api_key:
        raise ValueError("Gemini API key is not configured")

    model_name = gemini_cfg.get("default_model", "gemini-3.1-flash-lite")
    if any(m in model_name for m in ["gemini-1.5", "gemini-2.0"]):
        model_name = "gemini-3.1-flash-lite"

    max_retries = 3
    backoff_delays = [3.0, 7.0, 15.0]

    for attempt in range(max_retries):
        if attempt == 0 and rate_limiter:
            rate_limiter.wait_if_needed(max_wait_s=5.0)
        try:
            # 1. Try Google GenAI SDK first
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=api_key)
                resp = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=gemini_cfg.get("options", {}).get("temperature", 0.2),
                        tools=[],
                    ),
                )
                raw_text = resp.text or ""
                parsed = parse_json_func(raw_text)
                parsed["provider"] = f"gemini:{model_name}"
                return parsed
            except ImportError:
                logger.debug("google.genai SDK not imported; using HTTPS REST fallback.")
            except Exception as sdk_exc:
                err_str = str(sdk_exc)
                err_lower = err_str.lower()
                if "spending cap" in err_lower:
                    if rate_limiter:
                        rate_limiter.trigger_cooldown(60.0)
                    raise
                if any(k in err_lower for k in ("429", "503", "resource_exhausted", "quota exceeded", "high demand", "unavailable", "service unavailable")):
                    if attempt == max_retries - 1 and ("429" in err_lower or "resource_exhausted" in err_lower):
                        if rate_limiter:
                            rate_limiter.trigger_cooldown(60.0)
                    raise
                logger.warning("google.genai SDK call failed: %s; trying HTTPS REST fallback.", sdk_exc)

            # 2. HTTPS REST Fallback
            timeout = gemini_cfg.get("timeout_seconds", 30)
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.2,
                },
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                resp_json = json.loads(resp_body)
                candidates = resp_json.get("candidates", [])
                if not candidates:
                    raise RuntimeError("Gemini returned empty candidates")
                part = candidates[0].get("content", {}).get("parts", [{}])[0]
                raw_text = part.get("text", "")
                parsed = parse_json_func(raw_text)
                parsed["provider"] = f"gemini-rest:{model_name}"
                return parsed

        except urllib.error.HTTPError as http_err:
            if http_err.code in (429, 503):
                if attempt < max_retries - 1:
                    sleep_s = backoff_delays[attempt]
                    logger.warning(
                        "Gemini API rate/capacity limit HTTP %d encountered. Retrying in %.0fs (attempt %d/%d)...",
                        http_err.code, sleep_s, attempt + 1, max_retries,
                    )
                    time.sleep(sleep_s)
                    continue
                else:
                    if http_err.code == 429 and rate_limiter:
                        rate_limiter.trigger_cooldown(60.0)
            raise
        except Exception as exc:
            err_str = str(exc)
            err_lower = err_str.lower()
            if "spending cap" in err_lower or "api_key_invalid" in err_lower or "api key not valid" in err_lower:
                if rate_limiter:
                    rate_limiter.trigger_cooldown(60.0)
                raise
            if any(k in err_lower for k in ("429", "503", "resource_exhausted", "quota exceeded", "high demand", "unavailable", "service unavailable")):
                if attempt < max_retries - 1:
                    sleep_s = backoff_delays[attempt]
                    logger.warning(
                        "Gemini API rate/capacity limit (%s) encountered. Retrying in %.0fs (attempt %d/%d)...",
                        err_str[:80], sleep_s, attempt + 1, max_retries,
                    )
                    time.sleep(sleep_s)
                    continue
                else:
                    if ("429" in err_lower or "resource_exhausted" in err_lower) and rate_limiter:
                        rate_limiter.trigger_cooldown(60.0)
            raise

    raise RuntimeError("Gemini retries exhausted")


def test_gemini_connection(
    api_key: str | None = None,
    key_id: str | None = None,
    model: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Test Gemini API connection by querying the models list endpoint."""
    clean_key = ""
    cfg = config or {}
    if key_id:
        for k in cfg.get("saved_gemini_keys", []):
            if k.get("id") == key_id:
                clean_key = str(k.get("key", "")).strip()
                break

    if not clean_key and api_key and "****" not in api_key:
        clean_key = api_key.strip()

    if not clean_key:
        clean_key = str(cfg.get("gemini_api_key", "")).strip()

    if not clean_key or "__REPLACE_" in clean_key:
        return {
            "success": False,
            "message": "Gemini API key is required to test connection.",
            "models": [],
        }

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={clean_key}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "SPD-Analysis-Engine"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_models = data.get("models", [])
            models = [
                m["name"].replace("models/", "")
                for m in raw_models
                if "gemini" in m.get("name", "").lower()
            ]
            return {
                "success": True,
                "message": "Gemini API connection successful! Valid Gemini API key.",
                "models": models[:12],
            }
    except urllib.error.HTTPError as http_err:
        err_body = ""
        try:
            err_body = http_err.read().decode("utf-8")
        except Exception:
            pass
        scrubbed = err_body.replace(clean_key, "***") if clean_key else err_body
        msg = f"HTTP {http_err.code}"
        try:
            err_json = json.loads(scrubbed)
            msg = err_json.get("error", {}).get("message") or msg
        except Exception:
            pass
        return {
            "success": False,
            "message": f"Gemini connection failed ({http_err.code}): {msg}",
            "models": [],
        }
    except Exception as exc:
        scrubbed_exc = str(exc).replace(clean_key, "***") if clean_key else str(exc)
        return {
            "success": False,
            "message": f"Error connecting to Gemini API: {scrubbed_exc}",
            "models": [],
        }
```

---

### [20/27] File: `scripts\ai\provider_ollama.py`
- **Lines:** 91 | **Size:** 2.82 KB | **Type:** py

```python
"""
scripts/ai/provider_ollama.py
=============================
Local Ollama model provider integration and health checks.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)


def call_ollama(
    prompt: str,
    ollama_cfg: dict[str, Any],
    parse_json_func: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Issue HTTP POST to local Ollama instance."""
    base_url = ollama_cfg.get("base_url", "http://localhost:11434").rstrip("/")
    model = ollama_cfg.get("default_model", "qwen2.5-coder:7b")
    timeout = ollama_cfg.get("timeout_seconds", 60)
    options = ollama_cfg.get("options", {})

    url = f"{base_url}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": options,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp_body = resp.read().decode("utf-8")
        resp_json = json.loads(resp_body)
        raw_response = resp_json.get("response", "")
        parsed = parse_json_func(raw_response)
        parsed["provider"] = f"ollama:{model}"
        return parsed


def test_ollama_connection(
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Test connection to an Ollama instance by querying /api/tags."""
    clean_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
    url = f"{clean_url}/api/tags"

    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "SPD-Analysis-Engine"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name") for m in data.get("models", []) if m.get("name")]
            return {
                "success": True,
                "message": f"Connected to Ollama at {clean_url}.",
                "models": models,
                "installed_models": models,
                "model_found": (model in models) if model else True,
            }
    except urllib.error.URLError as url_err:
        return {
            "success": False,
            "message": f"Could not connect to Ollama at {clean_url} ({url_err.reason}). Ensure 'ollama serve' is running.",
            "models": [],
            "installed_models": [],
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"Error connecting to Ollama: {exc}",
            "models": [],
            "installed_models": [],
        }
```

---

### [21/27] File: `scripts\ai\rate_limiter.py`
- **Lines:** 109 | **Size:** 3.78 KB | **Type:** py

```python
"""
scripts/ai/rate_limiter.py
==========================
Thread-safe rate limiter with rolling 60-second window and cooldown queue.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable


class RateLimitManager:
    """
    Thread-safe RPM/TPM rate limiter with rolling 60-second window and cooldown queue.
    """

    def __init__(self, max_rpm: int = 15) -> None:
        self.max_rpm = max_rpm
        self._lock = threading.Lock()
        self._request_timestamps: deque[float] = deque()
        self._cooling_down = False
        self._cooldown_expiry = 0.0

    def reset(self) -> None:
        """Reset rate limiter state (clears cooldown and timestamp history)."""
        with self._lock:
            self._request_timestamps.clear()
            self._cooling_down = False
            self._cooldown_expiry = 0.0

    def record_request(self) -> None:
        """Record an outbound API request timestamp."""
        now = time.monotonic()
        with self._lock:
            self._clean_window(now)
            self._request_timestamps.append(now)

    def _clean_window(self, now: float) -> None:
        """Remove request timestamps older than 60 seconds."""
        cutoff = now - 60.0
        while self._request_timestamps and self._request_timestamps[0] < cutoff:
            self._request_timestamps.popleft()

    def get_status(self) -> dict[str, Any]:
        """Return live rate-limit status including cooling_down and cooldown_remaining_s."""
        now = time.monotonic()
        with self._lock:
            self._clean_window(now)
            current_count = len(self._request_timestamps)
            is_cooling = False
            remaining_s = 0.0

            if self._cooling_down and now < self._cooldown_expiry:
                is_cooling = True
                remaining_s = max(0.0, self._cooldown_expiry - now)
            elif current_count >= self.max_rpm:
                is_cooling = True
                oldest = self._request_timestamps[0]
                remaining_s = max(1.0, 60.0 - (now - oldest))
            else:
                self._cooling_down = False

            return {
                "cooling_down": is_cooling,
                "cooldown_remaining_s": round(remaining_s, 1),
                "current_rpm": current_count,
                "max_rpm": self.max_rpm,
            }

    def trigger_cooldown(self, duration_s: float = 60.0) -> None:
        """Explicitly enforce a cooldown (e.g. after receiving a 429 response)."""
        now = time.monotonic()
        with self._lock:
            self._cooling_down = True
            self._cooldown_expiry = max(self._cooldown_expiry, now + duration_s)

    def wait_if_needed(self, progress_callback: Callable[[float], None] | None = None, max_wait_s: float = 60.0) -> float:
        """
        Check rate limit and sleep if cooling down.
        Invokes progress_callback(remaining_s) periodically during wait.
        Returns total seconds waited.
        """
        total_waited = 0.0
        max_steps = 120
        step_count = 0
        while step_count < max_steps:
            step_count += 1
            status = self.get_status()
            if not status["cooling_down"]:
                break
            remaining = status["cooldown_remaining_s"]
            if remaining <= 0 or total_waited >= max_wait_s:
                break
            if progress_callback:
                try:
                    progress_callback(remaining)
                except Exception:
                    pass
            sleep_chunk = min(1.0, remaining, max(0.0, max_wait_s - total_waited))
            if sleep_chunk <= 0:
                break
            time.sleep(sleep_chunk)
            total_waited += sleep_chunk

        self.record_request()
        return total_waited
```

---

### [22/27] File: `scripts\server\__init__.py`
- **Lines:** 15 | **Size:** 0.35 KB | **Type:** py

```python
"""
scripts/server/__init__.py
==========================
Package containing modularized HTTP route mixins for the SPD Analysis Engine.
"""

from .routes_projects import ProjectRoutesMixin
from .routes_ai import AIRoutesMixin
from .routes_trash import TrashRoutesMixin

__all__ = [
    "ProjectRoutesMixin",
    "AIRoutesMixin",
    "TrashRoutesMixin",
]
```

---

### [23/27] File: `scripts\server\routes_ai.py`
- **Lines:** 393 | **Size:** 16.88 KB | **Type:** py

```python
"""
scripts/server/routes_ai.py
===========================
AI configuration and analysis routes mixin for the SPD Analysis Engine.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

try:
    from spd_analysis_engine.scripts.storage_rotator import SessionDB, _slug
    from spd_analysis_engine.scripts.ai_analyzer import AISynthesizer
except (ImportError, ModuleNotFoundError):
    from scripts.storage_rotator import SessionDB, _slug
    from scripts.ai_analyzer import AISynthesizer

logger = logging.getLogger(__name__)


def _now_ist_iso() -> str:
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%dT%H:%M:%S+05:30")


class AIRoutesMixin:
    """
    Mixin containing AI configuration, event analysis, command explanation,
    and batch synthesis route handlers.
    Expected to be mixed into _EngineRequestHandler.
    """

    def _handle_api_get_ai_config(self) -> None:
        """Return stored AI configuration settings with secrets masked."""
        engine = self.server_instance  # type: ignore[attr-defined]
        cfg = AISynthesizer.load_config(engine_root=engine.engine_root)
        masked_cfg = AISynthesizer.mask_config(cfg)
        self._send_json({"success": True, "config": masked_cfg})  # type: ignore[attr-defined]

    def _handle_api_save_ai_config(self, body: dict[str, Any]) -> None:
        """Save updated AI configuration settings."""
        engine = self.server_instance  # type: ignore[attr-defined]
        current_cfg = AISynthesizer.load_config(engine_root=engine.engine_root)
        new_cfg = dict(current_cfg)

        if "preferred_provider" in body:
            new_cfg["preferred_provider"] = str(body["preferred_provider"]).strip() or "heuristic"
        if "ollama_model" in body:
            new_cfg["ollama_model"] = str(body["ollama_model"]).strip() or "qwen2.5-coder:7b"
        if "gemini_model" in body:
            new_cfg["gemini_model"] = str(body["gemini_model"]).strip() or "gemini-3.1-flash-lite"
        if "ollama_base_url" in body:
            new_cfg["ollama_base_url"] = str(body["ollama_base_url"]).strip() or "http://127.0.0.1:11434"

        if "select_key_id" in body:
            new_cfg["select_key_id"] = str(body["select_key_id"]).strip()
        if "delete_key_id" in body:
            new_cfg["delete_key_id"] = str(body["delete_key_id"]).strip()
        if "key_label" in body:
            new_cfg["key_label"] = str(body["key_label"]).strip()

        raw_key = str(body.get("gemini_api_key", "")).strip()
        if raw_key and "****" not in raw_key:
            new_cfg["gemini_api_key"] = raw_key
            new_cfg.setdefault("gemini", {})["api_key"] = raw_key
        elif raw_key == "" and "gemini_api_key" in body and not body.get("select_key_id"):
            new_cfg["gemini_api_key"] = ""
            new_cfg.setdefault("gemini", {})["api_key"] = ""

        saved = AISynthesizer.save_config(new_cfg, engine_root=engine.engine_root)
        masked_cfg = AISynthesizer.mask_config(saved)
        self._send_json({"success": True, "message": "AI settings saved successfully.", "config": masked_cfg})  # type: ignore[attr-defined]

    def _handle_api_test_ai_connection(self, body: dict[str, Any]) -> None:
        """Test connection to an AI provider (Ollama, Gemini, Heuristic)."""
        engine = self.server_instance  # type: ignore[attr-defined]
        provider = body.get("provider", "ollama")

        if provider == "ollama":
            base_url = body.get("base_url") or None
            model = body.get("model") or None
            res = AISynthesizer.test_ollama_connection(
                base_url=base_url,
                model=model,
                engine_root=engine.engine_root,
            )
            self._send_json(res)  # type: ignore[attr-defined]
        elif provider == "gemini":
            api_key = body.get("api_key") or None
            if api_key and "****" in api_key:
                api_key = None
            key_id = body.get("key_id") or None
            model = body.get("model") or None
            res = AISynthesizer.test_gemini_connection(
                api_key=api_key,
                key_id=key_id,
                model=model,
                engine_root=engine.engine_root,
            )
            self._send_json(res)  # type: ignore[attr-defined]
        elif provider in ("heuristic", "none"):
            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "provider": "heuristic",
                "message": "Zero-AI Heuristic Mode is ready (offline rule-based, zero network or GPU load).",
            })
        else:
            self._send_error(f"Unknown AI provider '{provider}'.", status=400)  # type: ignore[attr-defined]

    def _handle_api_analyze_event(self, project_name: str, event_id_str: str, body: dict[str, Any]) -> None:
        """Run AISynthesizer on an event and store the result in session.db."""
        try:
            event_id = int(event_id_str)
        except ValueError:
            self._send_error(f"Invalid event ID: {event_id_str}", status=400)  # type: ignore[attr-defined]
            return

        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        # Search current, last_run, history
        db_path = engine.engine_root / "current" / slug / "session.db"
        if not db_path.exists():
            db_path = engine.engine_root / "last_run" / slug / "session.db"
        if not db_path.exists():
            hist_dir = engine.engine_root / "history" / slug
            if hist_dir.exists():
                runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
                if runs:
                    db_path = runs[-1] / "session.db"

        if not db_path.exists():
            self._send_error(f"No session database found for project '{slug}'", status=404)  # type: ignore[attr-defined]
            return

        try:
            db = SessionDB(db_path)
            conn = db.conn
            assert conn is not None
            cur = conn.cursor()
            cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            erow = cur.fetchone()
            if not erow:
                db.close()
                self._send_error(f"Event #{event_id} not found in database", status=404)  # type: ignore[attr-defined]
                return

            event_dict = dict(erow)
            cur.execute("SELECT file_path, diff_content FROM patches WHERE event_id = ?", (event_id,))
            prows = cur.fetchall()
            patches = [dict(p) for p in prows]

            syn = AISynthesizer(engine.engine_root)
            analysis = syn.analyze_event(event_dict, patches)

            # Persist to database
            db.update_event_ai_summary(event_id, json.dumps(analysis))
            db.close()

            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "event_id": event_id,
                "ai_summary": analysis,
            })
        except Exception as exc:
            logger.exception("Failed to analyze event #%s for %s", event_id, slug)
            self._send_error(f"Error during AI analysis: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_analyze_exec(self, project_name: str, event_id_str: str, body: dict[str, Any]) -> None:
        """Run AISynthesizer.explain_command on an EXEC event and store the result in session.db."""
        try:
            event_id = int(event_id_str)
        except ValueError:
            self._send_error(f"Invalid event ID: {event_id_str}", status=400)  # type: ignore[attr-defined]
            return

        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        db_path = self._resolve_db_path(slug)  # type: ignore[attr-defined]

        if not db_path or not db_path.exists():
            self._send_error(f"No session database found for project '{slug}'", status=404)  # type: ignore[attr-defined]
            return

        try:
            db = SessionDB(db_path)
            conn = db.conn
            assert conn is not None
            cur = conn.cursor()
            cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            erow = cur.fetchone()
            if not erow:
                db.close()
                self._send_error(f"Event #{event_id} not found in database", status=404)  # type: ignore[attr-defined]
                return

            event_dict = dict(erow)
            command_str = event_dict.get("first_file_touched") or event_dict.get("summary") or ""

            # Extract exit_code and duration_s from body or parse from summary string
            exit_code = body.get("exit_code")
            duration_s = body.get("duration_s")
            summary_str = event_dict.get("summary") or ""

            if exit_code is None:
                m_exit = re.search(r"exit=(\d+)", summary_str)
                exit_code = int(m_exit.group(1)) if m_exit else 0
            if duration_s is None:
                m_dur = re.search(r"duration=([\d\.]+)s", summary_str)
                duration_s = float(m_dur.group(1)) if m_dur else 0.0

            # Gather surrounding events for context
            cur.execute(
                "SELECT id, event_type, first_file_touched, summary FROM events "
                "WHERE session_id = ? AND id != ? AND id BETWEEN ? AND ? ORDER BY id ASC",
                (event_dict.get("session_id", 1), event_id, max(1, event_id - 3), event_id + 3),
            )
            surrounding = [dict(r) for r in cur.fetchall()]

            syn = AISynthesizer(engine.engine_root)
            explanation = syn.explain_command(
                project_name=slug,
                command_str=command_str,
                exit_code=int(exit_code),
                duration_s=float(duration_s),
                surrounding_burst_context=surrounding,
                provider=body.get("provider"),
            )

            # Persist to database
            db.update_event_ai_summary(event_id, json.dumps(explanation))
            db.close()

            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "event_id": event_id,
                "ai_summary": explanation,
            })
        except Exception as exc:
            logger.exception("Failed to explain EXEC event #%s for %s", event_id, slug)
            self._send_error(f"Error during command explanation: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_analyze_batch(self, project_name: str, body: dict[str, Any]) -> None:
        """Asynchronously batch analyze un-analyzed events in a project session."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        db_path = self._resolve_db_path(slug)  # type: ignore[attr-defined]
        if not db_path or not db_path.exists():
            self._send_error(f"No session database found for project '{slug}'", status=404)  # type: ignore[attr-defined]
            return

        with engine._batch_jobs_lock:
            existing_job = engine._batch_jobs.get(slug)
            if existing_job and existing_job.get("status") == "running":
                self._send_json({  # type: ignore[attr-defined]
                    "success": True,
                    "job_id": existing_job.get("job_id"),
                    "project": slug,
                    "total_events": existing_job.get("total_events", 0),
                    "analyzed_count": existing_job.get("analyzed_count", 0),
                    "current_index": existing_job.get("current_index", 0),
                    "current_event_id": existing_job.get("current_event_id"),
                    "status": "running",
                    "message": "Batch analysis already running in background.",
                })
                return

        force_all = bool(body.get("force_all", False))
        requested_ids = body.get("event_ids")

        try:
            db = SessionDB(db_path)
            conn = db.conn
            assert conn is not None
            cur = conn.cursor()

            # Ensure ai_summary column exists
            cur.execute("PRAGMA table_info(events)")
            cols = [r["name"] for r in cur.fetchall()]
            if "ai_summary" not in cols:
                cur.execute("ALTER TABLE events ADD COLUMN ai_summary TEXT")
                conn.commit()

            if requested_ids and isinstance(requested_ids, list):
                placeholders = ",".join("?" for _ in requested_ids)
                cur.execute(f"SELECT id FROM events WHERE id IN ({placeholders}) ORDER BY id ASC", [int(x) for x in requested_ids])
            elif force_all:
                cur.execute("SELECT id FROM events ORDER BY id ASC")
            else:
                cur.execute("SELECT id FROM events WHERE ai_summary IS NULL OR ai_summary = '' ORDER BY id ASC")
            unresolved = cur.fetchall()
            event_ids = [int(r["id"]) for r in unresolved]
            db.close()

            job_id = f"batch_{slug}_{int(time.time())}"
            if not event_ids:
                job_info = {
                    "job_id": job_id,
                    "project": slug,
                    "status": "completed",
                    "total_events": 0,
                    "analyzed_count": 0,
                    "current_index": 0,
                    "current_event_id": None,
                    "started_at": _now_ist_iso(),
                    "completed_at": _now_ist_iso(),
                    "error": None,
                }
                with engine._batch_jobs_lock:
                    engine._batch_jobs[slug] = job_info
                self._send_json({  # type: ignore[attr-defined]
                    "success": True,
                    "job_id": job_id,
                    "project": slug,
                    "total_events": 0,
                    "analyzed_count": 0,
                    "status": "completed",
                    "message": "All events have already been analyzed.",
                })
                return

            job_info = {
                "job_id": job_id,
                "project": slug,
                "status": "running",
                "total_events": len(event_ids),
                "analyzed_count": 0,
                "current_index": 0,
                "current_event_id": event_ids[0],
                "started_at": _now_ist_iso(),
                "completed_at": None,
                "error": None,
            }
            with engine._batch_jobs_lock:
                engine._batch_jobs[slug] = job_info

            target_path = self._resolve_target_path(slug)  # type: ignore[attr-defined]
            t = threading.Thread(
                target=engine._run_async_batch,
                args=(slug, job_id, event_ids, db_path, target_path),
                daemon=True,
            )
            t.start()

            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "job_id": job_id,
                "project": slug,
                "total_events": len(event_ids),
                "status": "running",
                "message": f"Started async analysis for {len(event_ids)} bursts in the background.",
            })
        except Exception as exc:
            logger.exception("Failed to start async batch analysis for %s", slug)
            self._send_error(f"Error starting batch AI analysis: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_analyze_batch_status(self, project_name: str) -> None:
        """Return the current progress and status of async batch analysis."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        with engine._batch_jobs_lock:
            job = engine._batch_jobs.get(slug)
            rate_status = AISynthesizer.rate_limiter.get_status()
            if not job:
                self._send_json({  # type: ignore[attr-defined]
                    "project": slug,
                    "status": "idle",
                    "analyzed_count": 0,
                    "total_events": 0,
                    "cooling_down": rate_status.get("cooling_down", False),
                    "cooldown_remaining_s": rate_status.get("cooldown_remaining_s", 0.0),
                    "message": "No active batch analysis job.",
                })
                return
            res_dict = {
                "project": slug,
                **job,
            }
            if rate_status.get("cooling_down"):
                res_dict["cooling_down"] = True
                res_dict["cooldown_remaining_s"] = rate_status.get("cooldown_remaining_s", 0.0)
            elif "cooling_down" not in res_dict:
                res_dict["cooling_down"] = False
                res_dict["cooldown_remaining_s"] = 0.0
            self._send_json(res_dict)  # type: ignore[attr-defined]
```

---

### [24/27] File: `scripts\server\routes_projects.py`
- **Lines:** 932 | **Size:** 41.56 KB | **Type:** py

```python
"""
scripts/server/routes_projects.py
=================================
Project lifecycle, workspace events, logs, Git operations, and rollback
routes mixin for the SPD Analysis Engine.
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

logger = logging.getLogger(__name__)

_NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


_utcnow_iso = _now_ist_iso


class ProjectRoutesMixin:
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
        debounce = float(body.get("debounce", 3.5))
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

    def _handle_api_resume_project(self, project_name: str) -> None:
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
            resolved_p = self._resolve_target_path(slug)
            if resolved_p:
                target_path = str(resolved_p)

        if not target_path:
            self._send_error(f"Cannot resume '{slug}': project.meta not found.", status=404)  # type: ignore[attr-defined]
            return

        target_p = Path(target_path)
        if not target_p.exists():
            self._send_error(f"Target workspace '{target_path}' does not exist on disk.", status=400)  # type: ignore[attr-defined]
            return

        # Attempt to load saved options from worker.status.json
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

        status_candidates = [
            engine.engine_root / "last_run" / slug / "worker.status.json",
            engine.engine_root / "current" / slug / "worker.status.json",
        ]
        for sf in status_candidates:
            if sf.exists():
                try:
                    s_data = json.loads(sf.read_text(encoding="utf-8"))
                    powers = s_data.get("powers", {})
                    track_reads = powers.get("reads", True)
                    track_exec = powers.get("exec", True)
                    shadow_git = powers.get("shadow_git", True)
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

    def _handle_api_events(self, project_name: str) -> None:
        """Fetch all recorded events and patches from the project session database across all sessions."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        # Consolidate historical runs so all prompt bursts (#1 through #N) are in the canonical database
        consolidated_path = consolidate_project_history(engine.engine_root, slug)

        # Search current, last_run, history
        db_path = engine.engine_root / "current" / slug / "session.db"
        tier = "current"
        if not db_path.exists():
            db_path = engine.engine_root / "last_run" / slug / "session.db"
            tier = "last_run"
        if not db_path.exists() and consolidated_path and consolidated_path.exists():
            db_path = consolidated_path
            tier = "history"
        if not db_path.exists():
            hist_dir = engine.engine_root / "history" / slug
            if hist_dir.exists():
                runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
                if runs:
                    db_path = runs[-1] / "session.db"
                    tier = "history"

        if not db_path.exists():
            self._send_json({"project": slug, "tier": None, "events": []})  # type: ignore[attr-defined]
            return

        try:
            events_data = engine._read_events_from_db(db_path)
            sessions_data = engine._read_sessions_from_db(db_path)
            self._send_json({  # type: ignore[attr-defined]
                "project": slug,
                "tier": tier,
                "db_path": str(db_path),
                "events": events_data,
                "sessions": sessions_data,
            })
        except Exception as exc:
            logger.exception("Failed to query events for %s", slug)
            self._send_error(f"Error querying session database: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_sessions(self, project_name: str) -> None:
        """Fetch all sessions and burst metrics for a project."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        db_path = engine.engine_root / "current" / slug / "session.db"
        tier = "current"
        if not db_path.exists():
            db_path = engine.engine_root / "last_run" / slug / "session.db"
            tier = "last_run"
        if not db_path.exists():
            hist_dir = engine.engine_root / "history" / slug
            if hist_dir.exists():
                runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
                if runs:
                    db_path = runs[-1] / "session.db"
                    tier = "history"

        if not db_path.exists():
            self._send_json({"project": slug, "tier": None, "sessions": []})  # type: ignore[attr-defined]
            return

        try:
            sessions = engine._read_sessions_from_db(db_path)
            self._send_json({  # type: ignore[attr-defined]
                "project": slug,
                "tier": tier,
                "db_path": str(db_path),
                "sessions": sessions,
            })
        except Exception as exc:
            logger.exception("Failed to query sessions for %s", slug)
            self._send_error(f"Error querying sessions: {exc}", status=500)  # type: ignore[attr-defined]

    _handle_api_project_sessions = _handle_api_sessions

    def _handle_api_logs(self, project_name: str) -> None:
        """Read the last 150 lines from worker.log."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        log_path = engine.engine_root / "current" / slug / "worker.log"
        if not log_path.exists():
            log_path = engine.engine_root / "last_run" / slug / "worker.log"

        if not log_path.exists():
            self._send_json({"project": slug, "lines": [], "total_lines": 0})  # type: ignore[attr-defined]
            return

        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
            all_lines = content.splitlines()
            recent_lines = all_lines[-150:] if len(all_lines) > 150 else all_lines
            self._send_json({  # type: ignore[attr-defined]
                "project": slug,
                "lines": recent_lines,
                "total_lines": len(all_lines),
            })
        except Exception as exc:
            self._send_error(f"Error reading log file: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_export(self, project_name: str) -> None:
        """Generate an exportable JSON payload of all session events and patches."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        db_path = engine.engine_root / "current" / slug / "session.db"
        tier = "current"
        if not db_path.exists():
            db_path = engine.engine_root / "last_run" / slug / "session.db"
            tier = "last_run"

        if not db_path.exists():
            self._send_error(f"No session database found for project '{slug}'", status=404)  # type: ignore[attr-defined]
            return

        try:
            events = engine._read_events_from_db(db_path)
            export_payload = {
                "engine": "SPD Analysis Engine",
                "version": "1.0.0",
                "project": slug,
                "tier": tier,
                "exported_at": _utcnow_iso(),
                "total_events": len(events),
                "events": events,
            }
            self._send_json(export_payload)  # type: ignore[attr-defined]
        except Exception as exc:
            self._send_error(f"Failed to export session: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_shadow_git(self, project_name: str) -> None:
        """Return micro-commit history from the project's shadow git repo."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        shadow_dir = engine.engine_root / "current" / slug / "shadow_git"
        if not shadow_dir.exists():
            shadow_dir = engine.engine_root / "last_run" / slug / "shadow_git"

        if not shadow_dir.exists():
            self._send_json({"project": slug, "commits": [], "has_shadow_git": False})  # type: ignore[attr-defined]
            return

        try:
            sg = ShadowGit(shadow_dir=shadow_dir, target_dir=engine.engine_root)
            commits = sg.get_commit_history(max_count=50)
            self._send_json({  # type: ignore[attr-defined]
                "project": slug,
                "commits": commits,
                "has_shadow_git": True,
                "total_commits": len(commits),
            })
        except Exception as exc:
            logger.exception("Failed to get shadow git history for %s", slug)
            self._send_error(f"Error querying shadow git: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_record_exec(self, project_name: str, body: dict[str, Any]) -> None:
        """Record an execution event in session.db."""
        command = str(body.get("command", "")).strip()
        if not command:
            self._send_error("Parameter 'command' is required.", status=400)  # type: ignore[attr-defined]
            return

        duration_s = float(body.get("duration_s", 0.0))
        exit_code = int(body.get("exit_code", 0))

        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        db_path = engine.engine_root / "current" / slug / "session.db"
        if not db_path.exists():
            self._send_error(f"Active session database not found for '{slug}'.", status=404)  # type: ignore[attr-defined]
            return

        try:
            db = SessionDB(db_path)
            cur = db.conn.cursor()
            cur.execute("SELECT id FROM sessions WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            session_id = row[0] if row else 1
            tracker = ExecutionTracker(target_dir=engine.engine_root, db=db, session_id=session_id)
            event_id = tracker.record_execution(command, duration_s=duration_s, exit_code=exit_code)
            db.close()
            self._send_json({"status": "recorded", "event_id": event_id})  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Failed to record exec event for %s", slug)
            self._send_error(f"Error recording exec event: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_get_git_config(self) -> None:
        """Return stored GitHub credential settings with token masked."""
        engine = self.server_instance  # type: ignore[attr-defined]
        cfg = GitCredentialManager.load_config(engine_root=engine.engine_root)
        masked_cfg = GitCredentialManager.mask_token(cfg)
        self._send_json({"success": True, "config": masked_cfg})  # type: ignore[attr-defined]

    def _handle_api_save_git_config(self, body: dict[str, Any]) -> None:
        """Save updated GitHub credential settings."""
        engine = self.server_instance  # type: ignore[attr-defined]
        current_cfg = GitCredentialManager.load_config(engine_root=engine.engine_root)

        new_cfg = dict(current_cfg)
        if "github_username" in body:
            new_cfg["github_username"] = str(body["github_username"]).strip()

        raw_token = str(body.get("github_token", "")).strip()
        if raw_token and "****" not in raw_token:
            new_cfg["github_token"] = raw_token
        elif raw_token == "" and "github_token" in body:
            new_cfg["github_token"] = ""

        if "default_remote" in body:
            new_cfg["default_remote"] = str(body["default_remote"]).strip() or "origin"
        if "default_branch" in body:
            new_cfg["default_branch"] = str(body["default_branch"]).strip() or "main"
        if "auto_generate_changelog" in body:
            new_cfg["auto_generate_changelog"] = bool(body["auto_generate_changelog"])

        saved = GitCredentialManager.save_config(new_cfg, engine_root=engine.engine_root)
        masked_cfg = GitCredentialManager.mask_token(saved)
        self._send_json({"success": True, "message": "GitHub settings saved successfully.", "config": masked_cfg})  # type: ignore[attr-defined]

    def _handle_api_test_git_connection(self, body: dict[str, Any]) -> None:
        """Test connection to a remote git repository."""
        engine = self.server_instance  # type: ignore[attr-defined]
        repo_url = body.get("repo_url")
        override_token = body.get("github_token")
        override_user = body.get("github_username")

        if override_token and "****" in override_token:
            override_token = None

        res = GitCredentialManager.test_connection(
            repo_url=repo_url,
            engine_root=engine.engine_root,
            token=override_token,
            username=override_user,
        )
        self._send_json(res)  # type: ignore[attr-defined]

    def _handle_api_create_git_repo(self, body: dict[str, Any]) -> None:
        """Create a remote repository on GitHub and optionally configure origin on the workspace."""
        engine = self.server_instance  # type: ignore[attr-defined]
        repo_name = str(body.get("repo_name", "")).strip()
        project_name = str(body.get("project_name", "")).strip()
        private = bool(body.get("private", False))
        description = body.get("description")

        if not repo_name:
            if project_name:
                repo_name = _slug(project_name)
            else:
                self._send_error("Parameter 'repo_name' is required.", status=400)  # type: ignore[attr-defined]
                return

        res = GitCredentialManager.create_remote_repo(
            repo_name=repo_name,
            private=private,
            engine_root=engine.engine_root,
            description=description,
        )

        if not res.get("success"):
            self._send_json(res, status=400)  # type: ignore[attr-defined]
            return

        # If project_name is provided, configure origin remote on the workspace
        remote_configured = False
        clone_url = res.get("clone_url")
        if project_name and clone_url:
            slug = _slug(project_name)
            target_path = self._resolve_target_path(slug)
            if target_path and target_path.exists():
                git_bin = shutil.which("git")
                if git_bin:
                    try:
                        # Ensure repo initialized
                        if not (target_path / ".git").exists():
                            subprocess.run([git_bin, "init", "-b", "main"], cwd=str(target_path), check=False, capture_output=True, creationflags=_NO_WINDOW_FLAG, shell=False)

                        # Check if origin remote exists
                        chk = subprocess.run([git_bin, "remote", "get-url", "origin"], cwd=str(target_path), capture_output=True, text=True, check=False, creationflags=_NO_WINDOW_FLAG, shell=False)
                        if chk.returncode == 0:
                            # Update origin
                            subprocess.run([git_bin, "remote", "set-url", "origin", clone_url], cwd=str(target_path), check=False, capture_output=True, creationflags=_NO_WINDOW_FLAG, shell=False)
                        else:
                            # Add origin
                            subprocess.run([git_bin, "remote", "add", "origin", clone_url], cwd=str(target_path), check=False, capture_output=True, creationflags=_NO_WINDOW_FLAG, shell=False)
                        remote_configured = True
                        update_project_meta(engine.engine_root, slug, remote_url=clone_url, remote_branch="main")
                    except Exception as exc:
                        logger.warning("Failed to configure remote 'origin' for %s: %s", slug, exc)

        res["remote_configured"] = remote_configured
        self._send_json(res)  # type: ignore[attr-defined]

    def _handle_api_prepare_sync(self, project_name: str, body: dict[str, Any]) -> None:
        """
        Synthesize session bursts into Conventional Commit title/body and CHANGELOG entry
        in preparation for remote push.
        """
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        target_path = self._resolve_target_path(slug)
        if not target_path or not target_path.exists():
            self._send_error(f"Target workspace for project '{slug}' could not be resolved or does not exist.", status=404)  # type: ignore[attr-defined]
            return

        db_path = self._resolve_db_path(slug)
        events: list[dict[str, Any]] = []
        if db_path and db_path.exists():
            events = engine._read_events_from_db(db_path)

        synthesizer = AISynthesizer(engine_root=engine.engine_root)
        changelog_res = synthesizer.generate_session_changelog(events)

        cfg = GitCredentialManager.load_config(engine_root=engine.engine_root)
        default_remote = cfg.get("default_remote", "origin")
        default_branch = cfg.get("default_branch", "main")
        auto_changelog = cfg.get("auto_generate_changelog", True)

        detected_remote_url = ""
        if (target_path / ".git").exists():
            try:
                cmd_res = subprocess.run(
                    ["git", "remote", "get-url", default_remote],
                    cwd=str(target_path),
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=_NO_WINDOW_FLAG,
                    shell=False,
                )
                if cmd_res.returncode == 0:
                    detected_remote_url = GitCredentialManager.scrub_text(cmd_res.stdout.strip())
            except Exception:
                pass

        # Check project.meta for persistent remote configuration
        meta = read_project_meta(engine.engine_root, slug)
        saved_remote = meta.get("remote_url", "")
        saved_branch = meta.get("remote_branch", "")
        if saved_remote:
            detected_remote_url = saved_remote
        if saved_branch:
            default_branch = saved_branch

        self._send_json({  # type: ignore[attr-defined]
            "success": True,
            "project": slug,
            "target_path": str(target_path),
            "commit_title": changelog_res.get("commit_title", "feat: session updates"),
            "commit_body": changelog_res.get("commit_body", "- Code modifications applied."),
            "changelog_entry": changelog_res.get("changelog_entry", ""),
            "provider": changelog_res.get("provider", "offline"),
            "remote_url": detected_remote_url or "",
            "remote": default_remote,
            "branch": default_branch,
            "auto_generate_changelog": auto_changelog,
            "events_count": len(events),
        })

    def _handle_api_git_push(self, project_name: str, body: dict[str, Any]) -> None:
        """Push workspace changes to remote git repository."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)

        remote_name = body.get("remote", "origin")
        remote_url = body.get("remote_url")
        commit_message = body.get("commit_message")
        changelog_content = body.get("changelog_content")
        branch = body.get("branch", "main")

        target_path = self._resolve_target_path(slug)
        if not target_path or not target_path.exists():
            self._send_error(f"Target workspace for project '{slug}' could not be resolved or does not exist.", status=404)  # type: ignore[attr-defined]
            return

        # If remote_url not provided, check project.meta
        meta = read_project_meta(engine.engine_root, slug)
        if not remote_url and meta.get("remote_url"):
            remote_url = meta["remote_url"]
        if not branch and meta.get("remote_branch"):
            branch = meta["remote_branch"]

        try:
            result = ShadowGit.push_to_remote(
                target_path,
                remote_url=remote_url,
                commit_message=commit_message,
                changelog_content=changelog_content,
                branch=branch,
                remote_name=remote_name,
                engine_root=engine.engine_root,
            )

            if result.get("success"):
                effective_remote = remote_url or result.get("remote", "")
                effective_branch = result.get("branch", branch)
                update_project_meta(
                    engine.engine_root,
                    slug,
                    remote_url=effective_remote,
                    remote_branch=effective_branch,
                    last_push_status="SUCCESS",
                    last_push_time=datetime.now(_IST).strftime("%Y-%m-%d %I:%M:%S %p IST"),
                    last_push_commit=result.get("commit", ""),
                )

            self._send_json({  # type: ignore[attr-defined]
                "project": slug,
                "target_path": str(target_path),
                **result,
            })
        except Exception as exc:
            logger.exception("Failed git push for %s", slug)
            self._send_error(f"Error during git push: {exc}", status=500)  # type: ignore[attr-defined]

    def _handle_api_rollback_burst(self, project_name: str, event_id: int) -> None:
        engine = self.server_instance  # type: ignore[attr-defined]
        try:
            try:
                from spd_analysis_engine.scripts.storage_rotator import rollback_burst
            except (ImportError, ModuleNotFoundError):
                from scripts.storage_rotator import rollback_burst
            res = rollback_burst(engine.engine_root, project_name, event_id)
            if res.get("success"):
                self._send_json(res)  # type: ignore[attr-defined]
            else:
                self._send_error(res.get("message", "Rollback failed"), status=400)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Error rolling back burst #%d for project %s", event_id, project_name)
            self._send_error(str(exc), status=500)  # type: ignore[attr-defined]

    def _handle_api_seal_burst(self, project_name: str) -> None:
        """Trigger immediate burst sealing (Lap button) for an active project."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        curr_dir = engine.engine_root / "current" / slug
        if not curr_dir.exists():
            self._send_error(f"Project '{slug}' is not active in current/.", status=400)  # type: ignore[attr-defined]
            return

        cmd_file = curr_dir / "seal_burst.cmd"
        try:
            cmd_file.write_text("SEAL\n", encoding="utf-8")
            # Poll up to 300ms for worker to process and delete the trigger file
            for _ in range(6):
                time.sleep(0.05)
                if not cmd_file.exists():
                    break
            self._send_json({  # type: ignore[attr-defined]
                "success": True,
                "project": slug,
                "message": "Burst sealed successfully.",
            })
        except Exception as exc:
            logger.exception("Error writing seal_burst.cmd for %s", slug)
            self._send_error(str(exc), status=500)  # type: ignore[attr-defined]

    def _handle_api_project_specs(self, project_name: str) -> None:
        """Return project specs, powers, debounce window, and GitHub remote info."""
        engine = self.server_instance  # type: ignore[attr-defined]
        slug = _slug(project_name)
        meta = read_project_meta(engine.engine_root, slug)

        target_path = self._resolve_target_path(slug)
        target_path_str = str(target_path) if target_path else meta.get("target_path", "")

        debounce = 3.5
        powers = {"track_edits": True, "track_reads": True, "track_exec": True, "shadow_git": True}
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
            "powers": powers,
            "ide_profile": ide_profile,
            "ide_custom_marker": ide_custom_marker,
            "remote_url": meta.get("remote_url", ""),
            "remote_branch": meta.get("remote_branch", "main"),
            "last_push_status": meta.get("last_push_status", "Never pushed"),
            "last_push_time": meta.get("last_push_time", ""),
            "last_push_commit": meta.get("last_push_commit", ""),
        })
```

---

### [25/27] File: `web\app.js`
- **Lines:** 3376 | **Size:** 137.84 KB | **Type:** js

```javascript
/**
 * SPD Analysis Engine — Web Control Portal Client Controller
 * Pure Vanilla JavaScript (Zero external framework/CDN dependencies)
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Global State
  // ---------------------------------------------------------------------------
  const state = {
    selectedProject: null,
    selectedTier: 'current',
    activeTab: 'dashboard',
    projectsData: { current: [], last_run: [], history: [] },
    eventsData: [],
    sessionsData: [],
    selectedSessionId: null,
    selectedEventId: null,
    selectedFilePath: null,

    autoScrollLogs: true,
    sseActive: false,
    eventSource: null,
    pollTimer: null,
    lastHbSeconds: 0,
    timelineFilter: 'all',
    expandedAiEventIds: new Set(),
    analyzingEventIds: new Set(),
    savedGeminiKeys: [],
  };

  // ---------------------------------------------------------------------------
  // DOM Elements Cache
  // ---------------------------------------------------------------------------
  const DOM = {
    // Navigation & Sidebar
    btnToggleSidebar: document.getElementById('btn-toggle-sidebar'),
    sidebar: document.querySelector('.sidebar'),
    sidebarToggleIcon: document.getElementById('sidebar-toggle-icon'),
    navButtons: document.querySelectorAll('.nav-item'),
    tabViews: document.querySelectorAll('.tab-view'),
    navEventCount: document.getElementById('nav-event-count'),
    navExecCount: document.getElementById('nav-exec-count'),

    // Powers Ribbon
    powerPillEdits: document.getElementById('power-pill-edits'),
    powerPillReads: document.getElementById('power-pill-reads'),
    powerPillExec: document.getElementById('power-pill-exec'),
    powerPillGit: document.getElementById('power-pill-git'),

    // Header
    headerProjectName: document.getElementById('header-project-name'),
    headerTierPill: document.getElementById('header-tier-pill'),
    headerPathVal: document.getElementById('header-path-val'),
    headerPid: document.getElementById('header-pid'),
    headerUptime: document.getElementById('header-uptime'),
    headerMem: document.getElementById('header-mem'),
    headerHb: document.getElementById('header-hb'),
    headerCompactTel: document.getElementById('header-compact-tel'),
    btnHeaderResume: document.getElementById('btn-header-resume'),
    btnHeaderStop: document.getElementById('btn-header-stop'),
    btnHeaderAiSettings: document.getElementById('btn-header-ai-settings'),
    btnHeaderGitSettings: document.getElementById('btn-header-git-settings'),
    btnHeaderPushGit: document.getElementById('btn-header-push-git'),
    btnHeaderTrash: document.getElementById('btn-header-trash'),
    headerTrashCount: document.getElementById('header-trash-count') || document.getElementById('badge-trash-count'),
    badgeTrashCount: document.getElementById('badge-trash-count') || document.getElementById('header-trash-count'),
    btnHeaderDeleteProj: document.getElementById('btn-header-delete-proj'),

    // Sidebar Projects & Accordions
    projectFilterInput: document.getElementById('project-filter-input'),
    btnRefreshProjects: document.getElementById('btn-refresh-projects'),
    countCurrent: document.getElementById('count-current'),
    countLastRun: document.getElementById('count-last-run'),
    countHistory: document.getElementById('count-history'),
    tierCurrentItems: document.getElementById('tier-current-items'),
    tierLastRunItems: document.getElementById('tier-last-run-items'),
    tierHistoryItems: document.getElementById('tier-history-items'),
    accordionCurrent: document.getElementById('accordion-current'),
    accordionLastRun: document.getElementById('accordion-last-run'),
    accordionHistory: document.getElementById('accordion-history'),

    // Dashboard View
    dashActiveCount: document.getElementById('dash-active-count'),
    dashEventCount: document.getElementById('dash-event-count'),
    dashPatchCount: document.getElementById('dash-patch-count'),
    detailSlug: document.getElementById('detail-slug'),
    detailTier: document.getElementById('detail-tier'),
    detailPath: document.getElementById('detail-path'),
    detailSessionId: document.getElementById('detail-session-id'),
    detailPid: document.getElementById('detail-pid'),
    detailMemory: document.getElementById('detail-memory'),
    dashRecentEvents: document.getElementById('dash-recent-events'),
    btnTelemetryResume: document.getElementById('btn-telemetry-resume'),
    btnExportSession: document.getElementById('btn-export-session'),
    btnViewAllTimeline: document.getElementById('btn-view-all-timeline'),
    btnBatchAnalyzeSession: document.getElementById('btn-batch-analyze-session'),

    // Project Configuration & Specs Card
    cardProjectSpecs: document.getElementById('card-project-specs'),
    specMonitoredPath: document.getElementById('spec-monitored-path'),
    specAiProfile: document.getElementById('spec-ai-profile'),
    specDebounceWindow: document.getElementById('spec-debounce-window'),
    specPowersChecklist: document.getElementById('spec-powers-checklist'),
    specCheckEdits: document.getElementById('spec-check-edits'),
    specCheckReads: document.getElementById('spec-check-reads'),
    specCheckExec: document.getElementById('spec-check-exec'),
    specCheckGit: document.getElementById('spec-check-git'),
    specGithubRemote: document.getElementById('spec-github-remote'),
    specLastPushStatus: document.getElementById('spec-last-push-status'),
    btnSpecsPushGh: document.getElementById('btn-specs-push-gh'),

    // Real-Time Live Activity Stream
    cardLiveActivity: document.getElementById('card-live-activity'),
    liveTickerFeed: document.getElementById('live-ticker-feed'),
    liveActionCount: document.getElementById('live-action-count'),

    // Debounce Burst Monitor
    cardDebounceMonitor: document.getElementById('card-debounce-monitor'),
    debounceMonitorTitle: document.getElementById('debounce-monitor-title'),
    debouncePulse: document.getElementById('debounce-pulse'),
    debounceStatusBadge: document.getElementById('debounce-status-badge'),
    btnSealBurstNow: document.getElementById('btn-seal-burst-now'),
    debounceProgressFill: document.getElementById('debounce-progress-fill'),
    debounceStatusDesc: document.getElementById('debounce-status-desc'),
    debounceCountdownText: document.getElementById('debounce-countdown-text'),

    // Timeline View
    timelineContainer: document.getElementById('timeline-container'),
    timelineFilterGroup: document.getElementById('timeline-filter-group'),
    timelineFilterPills: document.querySelectorAll('.filter-pill'),
    btnRefreshTimeline: document.getElementById('btn-refresh-timeline'),
    timelineReadsList: document.getElementById('timeline-reads-list'),
    timelineReadCount: document.getElementById('timeline-read-count'),

    // Terminal View
    terminalFeedContainer: document.getElementById('terminal-feed-container'),
    btnRefreshTerminal: document.getElementById('btn-refresh-terminal'),

    // Diff Viewer View
    diffSessionSelect: document.getElementById('diff-session-select'),
    diffBurstSelect: document.getElementById('diff-burst-select'),
    diffBurstCount: document.getElementById('diff-burst-count'),
    diffEventSelect: document.getElementById('diff-burst-select'),
    diffFileCount: document.getElementById('diff-file-count'),
    diffFilesList: document.getElementById('diff-files-list'),
    diffCurrentFileName: document.getElementById('diff-current-file-name'),
    diffCodeContainer: document.getElementById('diff-code-container'),
    statAdditions: document.getElementById('stat-additions'),
    statDeletions: document.getElementById('stat-deletions'),
    diffAiSlot: document.getElementById('diff-ai-slot'),


    // Logs View
    logsTerminal: document.getElementById('logs-terminal'),
    chkAutoscroll: document.getElementById('chk-autoscroll'),
    btnCopyLogs: document.getElementById('btn-copy-logs'),
    btnRefreshLogs: document.getElementById('btn-refresh-logs'),

    // Modal: New Session
    btnNewSession: document.getElementById('btn-new-session'),
    modalNewSession: document.getElementById('modal-new-session'),
    modalCloseBtn: document.getElementById('modal-close-btn'),
    modalCancelBtn: document.getElementById('modal-cancel-btn'),
    formNewSession: document.getElementById('form-new-session'),
    inpProjectName: document.getElementById('inp-project-name'),
    slugPreview: document.getElementById('slug-preview'),
    inpTargetPath: document.getElementById('inp-target-path'),
    chkScaffold: document.getElementById('chk-scaffold'),
    rngDebounce: document.getElementById('rng-debounce'),
    debounceValDisplay: document.getElementById('debounce-val-display'),
    selIdeProfile: document.getElementById('sel-ide-profile'),
    groupCustomIdeMarker: document.getElementById('group-custom-ide-marker'),
    inpCustomIdeMarker: document.getElementById('inp-custom-ide-marker'),
    modalError: document.getElementById('modal-error'),
    modalSpinner: document.getElementById('modal-spinner'),
    modalSubmitBtn: document.getElementById('modal-submit-btn'),
    chkPowerEdits: document.getElementById('chk-power-edits'),
    chkPowerReads: document.getElementById('chk-power-reads'),
    chkPowerExec: document.getElementById('chk-power-exec'),
    chkPowerShadowGit: document.getElementById('chk-power-shadow-git'),
    chkPowerGitInit: document.getElementById('chk-power-git-init'),

    // Modal: Git Settings
    modalGitSettings: document.getElementById('modal-git-settings'),
    modalGitSettingsClose: document.getElementById('modal-git-settings-close'),
    modalGitSettingsCancel: document.getElementById('modal-git-settings-cancel'),
    formGitSettings: document.getElementById('form-git-settings'),
    inpGitUsername: document.getElementById('inp-git-username'),
    inpGitToken: document.getElementById('inp-git-token'),
    inpGitDefaultRemote: document.getElementById('inp-git-default-remote'),
    inpGitDefaultBranch: document.getElementById('inp-git-default-branch'),
    chkGitAutoChangelog: document.getElementById('chk-git-auto-changelog'),
    inpGitTestUrl: document.getElementById('inp-git-test-url'),
    btnTestGitConnection: document.getElementById('btn-test-git-connection'),
    gitTestStatus: document.getElementById('git-test-status'),
    btnSaveGitConfig: document.getElementById('btn-save-git-config'),

    // Modal: Milestone Sync & Changelog Review
    modalSyncReview: document.getElementById('modal-sync-review'),
    modalSyncReviewClose: document.getElementById('modal-sync-review-close'),
    modalSyncCancel: document.getElementById('modal-sync-cancel'),
    btnConfirmSyncPush: document.getElementById('btn-confirm-sync-push'),
    syncEventsCount: document.getElementById('sync-events-count'),
    syncProviderBadge: document.getElementById('sync-provider-badge'),
    inpSyncCommitTitle: document.getElementById('inp-sync-commit-title'),
    inpSyncCommitBody: document.getElementById('inp-sync-commit-body'),
    inpSyncChangelogContent: document.getElementById('inp-sync-changelog-content'),
    inpSyncRemote: document.getElementById('inp-sync-remote'),
    inpSyncBranch: document.getElementById('inp-sync-branch'),
    chkSyncAutoCreateRepo: document.getElementById('chk-sync-auto-create-repo'),
    syncError: document.getElementById('sync-error'),
    syncSpinner: document.getElementById('sync-spinner'),

    // Push Stepper Progress Bar
    syncStepperContainer: document.getElementById('sync-stepper-container'),
    syncStepperFill: document.getElementById('sync-stepper-fill'),
    syncStepperMsg: document.getElementById('sync-stepper-msg'),
    step1: document.getElementById('step-1'),
    step2: document.getElementById('step-2'),
    step3: document.getElementById('step-3'),

    // Modal: AI Settings
    modalAiSettings: document.getElementById('modal-ai-settings'),
    modalAiSettingsClose: document.getElementById('modal-ai-settings-close'),
    modalAiSettingsCancel: document.getElementById('modal-ai-settings-cancel'),
    formAiSettings: document.getElementById('form-ai-settings'),
    selAiProvider: document.getElementById('sel-ai-provider'),
    boxProviderOllama: document.getElementById('box-provider-ollama'),
    boxProviderGemini: document.getElementById('box-provider-gemini'),
    boxProviderHeuristic: document.getElementById('box-provider-heuristic'),
    inpOllamaUrl: document.getElementById('inp-ollama-url'),
    inpOllamaModel: document.getElementById('inp-ollama-model'),
    btnTestOllama: document.getElementById('btn-test-ollama'),
    ollamaTestStatus: document.getElementById('ollama-test-status'),
    selGeminiKeyVault: document.getElementById('sel-gemini-key-vault'),
    btnDeleteGeminiKey: document.getElementById('btn-delete-gemini-key'),
    inpGeminiKey: document.getElementById('inp-gemini-key'),
    lblGeminiKey: document.getElementById('lbl-gemini-key'),
    helpGeminiKey: document.getElementById('help-gemini-key'),
    geminiActiveKeyInfo: document.getElementById('gemini-active-key-info'),
    selGeminiModel: document.getElementById('sel-gemini-model'),
    btnTestGemini: document.getElementById('btn-test-gemini'),
    geminiTestStatus: document.getElementById('gemini-test-status'),
    btnSaveAiConfig: document.getElementById('btn-save-ai-config'),

    // Batch Progress Box
    batchProgressContainer: document.getElementById('batch-progress-container'),
    batchSpinner: document.getElementById('batch-spinner'),
    batchProgressTitle: document.getElementById('batch-progress-title'),
    batchProgressPct: document.getElementById('batch-progress-pct'),
    btnBatchRetry: document.getElementById('btn-batch-retry'),
    batchProgressFill: document.getElementById('batch-progress-fill'),
    batchProgressDetail: document.getElementById('batch-progress-detail'),
    batchProgressTime: document.getElementById('batch-progress-time'),

    // Modal: Delete Project
    modalDeleteProject: document.getElementById('modal-delete-project'),
    modalDeleteProjectClose: document.getElementById('modal-delete-project-close'),
    modalDeleteProjectCancel: document.getElementById('modal-delete-project-cancel'),
    delModalProjectName: document.getElementById('del-modal-project-name'),
    deleteBurstsContainer: document.getElementById('delete-bursts-container'),
    deleteBurstsList: document.getElementById('delete-bursts-list'),
    btnSelectAllBursts: document.getElementById('btn-select-all-bursts'),
    deleteRemoteOption: document.getElementById('delete-remote-option'),
    chkDeleteRemoteGithub: document.getElementById('chk-delete-remote-github'),
    deleteProjectError: document.getElementById('delete-project-error'),
    deleteProjectSpinner: document.getElementById('delete-project-spinner'),
    btnConfirmDeleteProject: document.getElementById('btn-confirm-delete-project'),

    // Modal: Trash Bin
    modalTrashBin: document.getElementById('modal-trash-bin'),
    modalTrashBinClose: document.getElementById('modal-trash-bin-close'),
    btnEmptyTrash: document.getElementById('btn-empty-trash'),
    trashBinItemsContainer: document.getElementById('trash-bin-items-container'),
    trashBinError: document.getElementById('trash-bin-error'),

    // Toasts & Status
    toastContainer: document.getElementById('toast-container'),
    connStatus: document.getElementById('connection-status'),
    connText: document.getElementById('conn-text'),

    // Floating Global Job Tray
    globalJobTray: document.getElementById('global-job-tray'),
    traySpinner: document.getElementById('tray-spinner'),
    trayTitle: document.getElementById('tray-title'),
    trayCloseBtn: document.getElementById('tray-close-btn'),
    trayProjectName: document.getElementById('tray-project-name'),
    trayProgressBar: document.getElementById('tray-progress-bar'),
    trayCounts: document.getElementById('tray-counts'),
    trayPercent: document.getElementById('tray-percent'),
    trayCooldownBadge: document.getElementById('tray-cooldown-badge'),
    trayCooldownText: document.getElementById('tray-cooldown-text'),
  };

  // ---------------------------------------------------------------------------
  // Utilities & Helpers
  // ---------------------------------------------------------------------------

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function scrubSensitiveText(text) {
    if (!text || typeof text !== 'string') return text;
    return text
      // GitHub PATs
      .replace(/gh[pousr]_[A-Za-z0-9_]{16,255}/g, '[REDACTED_GH_TOKEN]')
      // Google / Gemini API Keys
      .replace(/AIza[0-9A-Za-z\-_]{35}/g, '[REDACTED_API_KEY]')
      // Generic Bearer tokens
      .replace(/Bearer\s+[A-Za-z0-9\-._~+/]+=*/gi, 'Bearer [REDACTED_TOKEN]')
      // Basic Auth / embedded URLs
      .replace(/:\/\/([^:\s]+):([^@\s]+)@/g, '://$1:[REDACTED_SECRET]@')
      // Private key headers
      .replace(/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g, '[REDACTED_PRIVATE_KEY]')
      // High-entropy generic tokens or secrets in key-value pairs
      .replace(/(?:api_key|apikey|secret|token|password|auth_token)\s*[:=]\s*['"][^\s'"]{8,}['"]/gi, (m) => {
        const parts = m.split(/[:=]/);
        return `${parts[0]}: "[REDACTED_CREDENTIAL]"`;
      });
  }

  function slugify(name) {
    return name
      .trim()
      .toLowerCase()
      .replace(/[^\w\s-]/g, '')
      .replace(/[\s-]+/g, '_')
      .slice(0, 64) || 'unnamed';
  }

  function formatUptime(seconds) {
    if (seconds == null || isNaN(seconds)) return '—';
    const s = Math.floor(seconds);
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    if (h > 0) return `${h}h ${m % 60}m`;
    if (m > 0) return `${m}m ${s % 60}s`;
    return `${s}s`;
  }

  function formatIST(timestampStr, includeDate = true) {
    if (!timestampStr) return '—';
    try {
      const d = new Date(timestampStr);
      if (isNaN(d.getTime())) return timestampStr;
      const options = {
        timeZone: 'Asia/Kolkata',
        hour12: true,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      };
      if (includeDate) {
        options.year = 'numeric';
        options.month = 'short';
        options.day = '2-digit';
      }
      return `${new Intl.DateTimeFormat('en-IN', options).format(d)} IST`;
    } catch (e) {
      return timestampStr;
    }
  }

  function formatISTTime(timestampStr) {
    if (!timestampStr) return '';
    try {
      if (typeof timestampStr === 'string' && timestampStr.endsWith('IST')) {
        return timestampStr;
      }
      const d = new Date(timestampStr);
      if (isNaN(d.getTime())) {
        if (typeof timestampStr === 'string' && timestampStr.includes(':')) {
          return `${timestampStr} IST`;
        }
        return timestampStr;
      }
      const formatted = new Intl.DateTimeFormat('en-IN', {
        timeZone: 'Asia/Kolkata',
        hour12: true,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }).format(d);
      return `${formatted} IST`;
    } catch (e) {
      return timestampStr;
    }
  }

  function showToast(message, type = 'success', title = null) {
    if (!DOM.toastContainer) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    let iconChar = '✓';
    let defaultTitle = 'Success';
    if (type === 'error') {
      iconChar = '✕';
      defaultTitle = 'Error';
    } else if (type === 'info') {
      iconChar = 'ℹ';
      defaultTitle = 'Information';
    } else if (type === 'warning') {
      iconChar = '⚠';
      defaultTitle = 'Notice';
    }

    const toastIcon = document.createElement('span');
    toastIcon.className = 'toast-icon';
    toastIcon.textContent = iconChar;

    const toastBody = document.createElement('div');
    toastBody.className = 'toast-body';

    const toastTitle = document.createElement('div');
    toastTitle.className = 'toast-title';
    toastTitle.textContent = title || defaultTitle;

    const toastMsg = document.createElement('div');
    toastMsg.className = 'toast-msg';
    toastMsg.textContent = message;

    toastBody.appendChild(toastTitle);
    toastBody.appendChild(toastMsg);

    const closeBtn = document.createElement('button');
    closeBtn.className = 'toast-close';
    closeBtn.innerHTML = '&times;';
    closeBtn.title = 'Dismiss';
    closeBtn.onclick = () => toast.remove();

    toast.appendChild(toastIcon);
    toast.appendChild(toastBody);
    toast.appendChild(closeBtn);

    DOM.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      toast.style.transition = 'all 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 4500);
  }

  // ---------------------------------------------------------------------------
  // API Calls
  // ---------------------------------------------------------------------------

  async function apiGet(endpoint) {
    const res = await fetch(endpoint);
    if (!res.ok) {
      let errMsg = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body.error) errMsg = body.error;
      } catch (_) {}
      throw new Error(errMsg);
    }
    return await res.json();
  }

  async function apiPost(endpoint, payload) {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let errMsg = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body.error) errMsg = body.error;
      } catch (_) {}
      throw new Error(errMsg);
    }
    return await res.json();
  }

  // ---------------------------------------------------------------------------
  // Projects Management
  // ---------------------------------------------------------------------------

  async function refreshProjects() {
    try {
      const data = await apiGet('/api/projects');
      state.projectsData = data;

      // Update counters
      DOM.countCurrent.textContent = data.current.length;
      DOM.countLastRun.textContent = data.last_run.length;
      DOM.countHistory.textContent = data.history.length;

      // If no project selected yet, pick first active current or first last_run
      if (!state.selectedProject) {
        if (data.current.length > 0) {
          selectProject(data.current[0].name, 'current');
        } else if (data.last_run.length > 0) {
          selectProject(data.last_run[0].name, 'last_run');
        } else if (data.history.length > 0) {
          selectProject(data.history[0].name, 'history');
        }
      }

      renderProjectsList();
      updateHeaderTelemetry();
    } catch (err) {
      console.error('Failed to load projects:', err);
    }
  }

  async function loadProjectSpecs(projectName) {
    if (!projectName || !DOM.cardProjectSpecs) return;
    try {
      const data = await apiGet(`/api/project/${encodeURIComponent(projectName)}/specs`);
      if (DOM.specMonitoredPath) {
        DOM.specMonitoredPath.textContent = data.target_path || '—';
        DOM.specMonitoredPath.title = data.target_path || '';
      }
      if (DOM.specAiProfile) {
        const prof = data.ide_profile || 'antigravity';
        let profLabel = 'Google Antigravity';
        if (prof === 'cursor') profLabel = 'Cursor AI';
        else if (prof === 'windsurf') profLabel = 'Windsurf / Codeium';
        else if (prof === 'claude_code') profLabel = 'Claude Code CLI';
        else if (prof === 'custom') {
          profLabel = data.ide_custom_marker ? `Custom (${data.ide_custom_marker})` : 'Custom AI';
        }
        DOM.specAiProfile.textContent = `${profLabel} (Native VS Code Ignored)`;
      }
      if (DOM.specDebounceWindow) {
        const dbVal = data.debounce != null ? data.debounce : 3.5;
        const dbBadge = dbVal === 3.5 ? ' (Standard Recommended)' : ' (Custom)';
        DOM.specDebounceWindow.textContent = `${dbVal}s${dbBadge}`;
      }
      const powers = data.powers || {};
      setPowerPillState(DOM.specCheckEdits, powers.track_edits ?? true);
      setPowerPillState(DOM.specCheckReads, powers.track_reads ?? true);
      setPowerPillState(DOM.specCheckExec, powers.track_exec ?? true);
      setPowerPillState(DOM.specCheckGit, powers.shadow_git ?? true);

      if (DOM.specGithubRemote) {
        if (data.remote_url && data.remote_url !== 'Not Linked') {
          const branchSuffix = data.remote_branch ? ` [${data.remote_branch}]` : '';
          DOM.specGithubRemote.innerHTML = `<span class="badge badge-success" style="font-weight: 600; margin-right: 6px; background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.4); padding: 1px 6px; border-radius: 4px;">✓ Linked</span><span>${escapeHtml(data.remote_url)}${escapeHtml(branchSuffix)}</span>`;
          DOM.specGithubRemote.title = `${data.remote_url}${branchSuffix}`;
        } else {
          DOM.specGithubRemote.textContent = 'Not Linked';
          DOM.specGithubRemote.title = '';
        }
      }

      if (DOM.specLastPushStatus) {
        if (data.last_push_time) {
          const timeFormatted = formatIST(data.last_push_time);
          const commitSuffix = data.last_push_commit ? ` • commit ${data.last_push_commit.slice(0, 7)}` : '';
          DOM.specLastPushStatus.textContent = `${data.last_push_status} at ${timeFormatted}${commitSuffix}`;
        } else {
          DOM.specLastPushStatus.textContent = data.last_push_status || 'Never pushed';
        }
      }
    } catch (err) {
      console.warn(`Could not load specs for ${projectName}:`, err);
    }
  }

  function selectProject(projectName, tier) {
    if (state.selectedProject !== projectName) {
      state.selectedEventId = null;
      state.selectedFilePath = null;
    }
    state.selectedProject = projectName;
    state.selectedTier = tier;
    renderProjectsList();
    updateHeaderTelemetry();
    loadProjectSpecs(projectName);
    loadProjectEvents(projectName);
    loadProjectLogs(projectName);
  }

  function renderProjectsList() {
    const filter = DOM.projectFilterInput.value.toLowerCase().trim();

    function renderGroup(items, container, tier) {
      container.innerHTML = '';
      const filtered = items.filter((p) => p.name.toLowerCase().includes(filter));
      if (filtered.length === 0) {
        container.innerHTML = `<div class="empty-hint">${filter ? 'No matching projects' : 'No entries'}</div>`;
        return;
      }

      filtered.forEach((p) => {
        const btn = document.createElement('button');
        const isSelected = state.selectedProject === p.name && state.selectedTier === tier;
        btn.className = `project-item ${isSelected ? 'active' : ''}`;

        const dot = p.is_active
          ? '<span class="pulse-dot"></span>'
          : (p.is_interrupted ? '<span class="static-dot" style="background: #ef4444; box-shadow: 0 0 6px #ef4444;"></span>' : '<span class="static-dot"></span>');
        const burstCount = p.events_count || 0;
        const evCount = burstCount > 0 ? `<span class="badge badge-subtle">${burstCount} ${burstCount === 1 ? 'burst' : 'bursts'}</span>` : '';
        const interruptedBadge = p.is_interrupted ? `<span class="badge badge-interrupted" title="${escapeHtml(p.interrupted_reason || 'Interrupted')}">⚠️ Interrupted</span>` : '';

        btn.innerHTML = `
          <div class="project-item-left">
            ${dot}
            <span>${escapeHtml(p.name)}</span>
          </div>
          <div style="display: flex; gap: 4px; align-items: center;">
            ${interruptedBadge}
            ${evCount}
          </div>
        `;

        btn.addEventListener('click', () => selectProject(p.name, tier));
        container.appendChild(btn);
      });
    }

    renderGroup(state.projectsData.current, DOM.tierCurrentItems, 'current');
    renderGroup(state.projectsData.last_run, DOM.tierLastRunItems, 'last_run');
    renderGroup(state.projectsData.history, DOM.tierHistoryItems, 'history');
  }

  function getSelectedProjectInfo() {
    if (!state.selectedProject) return null;
    const tierList = state.projectsData[state.selectedTier] || [];
    return tierList.find((p) => p.name === state.selectedProject) || null;
  }

  function formatPreciseCountdown(seconds) {
    if (seconds <= 0) return '00:00.0';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    const ms = Math.floor((seconds % 1) * 10);
    const minStr = String(mins).padStart(2, '0');
    const secStr = String(secs).padStart(2, '0');
    return `${minStr}:${secStr}.${ms}`;
  }

  function updateHeaderTelemetry() {
    const p = getSelectedProjectInfo();
    if (!p) {
      DOM.headerProjectName.textContent = 'No Project Selected';
      DOM.headerTierPill.textContent = 'NONE';
      DOM.headerTierPill.className = 'project-tier-pill stopped';
      DOM.headerPathVal.textContent = '—';
      DOM.headerPid.textContent = '—';
      DOM.headerUptime.textContent = '—';
      DOM.headerMem.textContent = '—';
      DOM.headerHb.textContent = '—';
      if (DOM.headerCompactTel) DOM.headerCompactTel.textContent = '⚪ Inactive';
      DOM.btnHeaderStop.disabled = true;
      DOM.btnHeaderStop.style.display = '';
      if (DOM.btnHeaderResume) DOM.btnHeaderResume.style.display = 'none';
      if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.style.display = 'none';
      return;
    }

    DOM.headerProjectName.textContent = p.name;
    DOM.headerPathVal.textContent = p.target_path || '—';

    if (p.is_active) {
      DOM.headerTierPill.textContent = 'ACTIVE';
      DOM.headerTierPill.className = 'project-tier-pill';
      DOM.btnHeaderStop.disabled = false;
      DOM.btnHeaderStop.style.display = '';
      if (DOM.btnHeaderResume) DOM.btnHeaderResume.style.display = 'none';
      if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.style.display = 'none';
    } else if (p.is_interrupted || p.status === 'INTERRUPTED') {
      DOM.headerTierPill.textContent = 'INTERRUPTED';
      DOM.headerTierPill.className = 'project-tier-pill interrupted';
      DOM.headerTierPill.title = 'Session was terminated unexpectedly (e.g. system reboot, task kill, power outage)';
      DOM.btnHeaderStop.disabled = true;
      DOM.btnHeaderStop.style.display = 'none';
      if (DOM.btnHeaderResume) DOM.btnHeaderResume.style.display = '';
      if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.style.display = '';
    } else {
      DOM.headerTierPill.textContent = state.selectedTier.toUpperCase();
      DOM.headerTierPill.className = 'project-tier-pill stopped';
      DOM.btnHeaderStop.disabled = true;
      DOM.btnHeaderStop.style.display = 'none';
      if (DOM.btnHeaderResume) DOM.btnHeaderResume.style.display = '';
      if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.style.display = '';
    }

    DOM.headerPid.textContent = p.pid != null ? p.pid : '—';
    DOM.headerUptime.textContent = p.uptime_s != null ? formatUptime(p.uptime_s) : '—';
    const memStr = p.memory_mb != null ? `${p.memory_mb.toFixed(1)} MB` : '—';
    DOM.headerMem.textContent = memStr;
    DOM.headerHb.textContent = p.is_active ? 'Live' : (p.is_interrupted ? 'Interrupted' : 'Stopped');
    if (DOM.headerCompactTel) {
      DOM.headerCompactTel.textContent = `${p.is_active ? '🟢 Live' : (p.is_interrupted ? '⚠️ Interrupted' : '⚪ Stopped')} • ${memStr}`;
    }

    // Powers Ribbon
    const powers = p.powers || {};
    setPowerPillState(DOM.powerPillEdits, powers.track_edits ?? true);
    setPowerPillState(DOM.powerPillReads, powers.track_reads ?? true);
    setPowerPillState(DOM.powerPillExec, powers.track_exec ?? true);
    setPowerPillState(DOM.powerPillGit, powers.shadow_git ?? true);

    // Dashboard Telemetry Card
    DOM.detailSlug.textContent = p.name;
    DOM.detailTier.textContent = p.is_interrupted ? 'INTERRUPTED (Power Loss / Terminated)' : state.selectedTier;
    DOM.detailPath.textContent = p.target_path || '—';
    DOM.detailSessionId.textContent = p.session_id != null ? `#${p.session_id}` : '—';
    DOM.detailPid.textContent = p.pid != null ? p.pid : '—';
    DOM.detailMemory.textContent = p.memory_mb != null ? `${p.memory_mb.toFixed(1)} MB` : '—';

    // Debounce Burst Monitor
    if (DOM.cardDebounceMonitor) {
      const db = p.debounce;
      if (p.is_active && db) {
        if (db.active) {
          const quiet = db.quiet_period || 3.5;
          const elapsed = db.elapsed_s || 0;
          const remaining = db.remaining_s || 0;
          const pct = Math.min(100, Math.max(8, (elapsed / quiet) * 100));

          DOM.debounceProgressFill.style.width = `${pct.toFixed(0)}%`;
          DOM.debounceProgressFill.classList.add('active');
          DOM.debouncePulse.className = 'debounce-pulse-indicator active';
          DOM.debounceStatusBadge.className = 'debounce-status-badge active';
          DOM.debounceStatusBadge.textContent = 'Debouncing Burst';
          if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = '';
          DOM.debounceStatusDesc.textContent = `Coalescing edits in ${db.files_count || 1} file(s) (${db.first_file || ''})…`;
          DOM.debounceCountdownText.textContent = `${formatPreciseCountdown(remaining)} remaining…`;
        } else {
          DOM.debounceProgressFill.style.width = '0%';
          DOM.debounceProgressFill.classList.remove('active');
          DOM.debouncePulse.className = 'debounce-pulse-indicator idle';
          DOM.debounceStatusBadge.className = 'debounce-status-badge idle';
          DOM.debounceStatusBadge.textContent = 'Idle / Listening';
          if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = 'none';
          DOM.debounceStatusDesc.textContent = 'Watching for file modifications…';
          DOM.debounceCountdownText.textContent = '—';
        }
      } else if (p.is_active) {
        DOM.debounceProgressFill.style.width = '0%';
        DOM.debounceProgressFill.classList.remove('active');
        DOM.debouncePulse.className = 'debounce-pulse-indicator idle';
        DOM.debounceStatusBadge.className = 'debounce-status-badge idle';
        DOM.debounceStatusBadge.textContent = 'Idle / Listening';
        if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = 'none';
        DOM.debounceStatusDesc.textContent = 'Watching for file modifications…';
        DOM.debounceCountdownText.textContent = '—';
      } else {
        DOM.debounceProgressFill.style.width = '0%';
        DOM.debounceProgressFill.classList.remove('active');
        DOM.debouncePulse.className = 'debounce-pulse-indicator stopped';
        DOM.debounceStatusBadge.className = 'debounce-status-badge stopped';
        DOM.debounceStatusBadge.textContent = p.is_interrupted ? '⚠️ Interrupted' : 'Stopped';
        if (DOM.btnSealBurstNow) DOM.btnSealBurstNow.style.display = 'none';
        DOM.debounceStatusDesc.textContent = p.is_interrupted
          ? 'Session was terminated unexpectedly (e.g. power loss, external kill)'
          : 'Session not running';
        DOM.debounceCountdownText.textContent = '—';
      }
    }

    // Metric cards
    DOM.dashActiveCount.textContent = state.projectsData.current.filter((x) => x.is_active).length;
    DOM.dashEventCount.textContent = p.events_count || 0;
  }

  function setPowerPillState(el, isActive) {
    if (!el) return;
    if (isActive) {
      el.classList.add('active');
      el.classList.remove('disabled');
    } else {
      el.classList.remove('active');
      el.classList.add('disabled');
    }
  }

  // ---------------------------------------------------------------------------
  // Events & Timeline Management
  // ---------------------------------------------------------------------------

  async function loadProjectEvents(projectName) {
    if (!projectName) return;
    try {
      const data = await apiGet(`/api/project/${encodeURIComponent(projectName)}/events`);
      state.eventsData = data.events || [];
      state.sessionsData = data.sessions || [];

      // Filter events by type
      const editEvents = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
      const readEvents = state.eventsData.filter((ev) => ev.event_type === 'READ');
      const execEvents = state.eventsData.filter((ev) => ev.event_type === 'EXEC');
      const gitEvents = state.eventsData.filter((ev) => ev.event_type === 'GIT');

      DOM.navEventCount.textContent = editEvents.length;
      if (DOM.navExecCount) DOM.navExecCount.textContent = execEvents.length;
      if (DOM.timelineReadCount) DOM.timelineReadCount.textContent = readEvents.length;
      DOM.dashEventCount.textContent = editEvents.length;
      const curProj = getSelectedProjectInfo();
      if (curProj && curProj.events_count !== editEvents.length) {
        curProj.events_count = editEvents.length;
        renderProjectsList();
      }

      // Count total patches
      let patchCount = 0;
      editEvents.forEach((ev) => {
        patchCount += ev.patches ? ev.patches.length : 0;
      });
      DOM.dashPatchCount.textContent = patchCount;

      renderTimelineFromState();
      renderAnalyzedFiles(readEvents);
      renderTerminal(execEvents);
      renderDashboardRecentEvents(editEvents);
      setupDiffViewerHierarchy(editEvents);
    } catch (err) {
      console.warn(`Could not load events for ${projectName}:`, err);
      state.eventsData = [];
      state.sessionsData = [];
      DOM.navEventCount.textContent = '0';
      if (DOM.navExecCount) DOM.navExecCount.textContent = '0';
      if (DOM.timelineReadCount) DOM.timelineReadCount.textContent = '0';
      DOM.dashEventCount.textContent = '0';
      DOM.dashPatchCount.textContent = '0';
      renderTimelineFromState();
      renderAnalyzedFiles([]);
      renderTerminal([]);
      renderDashboardRecentEvents([]);
      setupDiffViewerHierarchy([]);
    }
  }

  async function loadProjectSessions(projectName) {
    if (!projectName) return;
    try {
      const data = await apiGet(`/api/project/${encodeURIComponent(projectName)}/sessions`);
      state.sessionsData = data.sessions || [];
      const editEvents = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
      setupDiffViewerHierarchy(editEvents);
    } catch (err) {
      console.warn(`Could not load sessions for ${projectName}:`, err);
    }
  }

  function setupTimelineFilters() {
    if (!DOM.timelineFilterGroup) return;
    if (!DOM.timelineFilterPills) return;
    DOM.timelineFilterPills.forEach((pill) => {
      pill.addEventListener('click', () => {
        DOM.timelineFilterPills.forEach((p) => p.classList.remove('active'));
        pill.classList.add('active');
        state.timelineFilter = pill.dataset.filter || 'all';
        renderTimelineFromState();
      });
    });
  }

  function renderTimelineFromState() {
    if (!DOM.timelineContainer) return;
    const gitEvents = state.eventsData.filter((ev) => ev.event_type === 'GIT');
    let eventsToRender = state.eventsData;

    if (state.timelineFilter === 'EDIT') {
      eventsToRender = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
    } else if (state.timelineFilter === 'READ') {
      eventsToRender = state.eventsData.filter((ev) => ev.event_type === 'READ');
    } else if (state.timelineFilter === 'EXEC') {
      eventsToRender = state.eventsData.filter((ev) => ev.event_type === 'EXEC');
    } else {
      // 'all' - exclude standalone GIT events since they are badged onto bursts
      eventsToRender = state.eventsData.filter((ev) => ev.event_type !== 'GIT');
    }

    renderTimeline(eventsToRender, gitEvents);
  }

  function createAiCardHtml(ai) {
    if (!ai) return '';
    const intent = ai.intent || 'No intent description provided.';
    const archImpact = ai.architecture_impact || '';
    const gains = ai.functionality_gained || 'No functional gains specified.';
    const provider = ai.provider ? `Provider: ${ai.provider}` : 'AI Intelligence';
    const cachedBadge = ai.cached ? ' • (Cached)' : '';

    let archHtml = '';
    if (archImpact) {
      archHtml = `
        <div class="ai-section">
          <div class="ai-section-title">Architectural Impact:</div>
          <div class="ai-gains-text" style="color: #38bdf8;">${escapeHtml(archImpact)}</div>
        </div>
      `;
    }

    let summaryHtml = '';
    const modifications = (Array.isArray(ai.key_modifications) && ai.key_modifications.length > 0)
      ? ai.key_modifications
      : (Array.isArray(ai.summary) && ai.summary.length > 0 ? ai.summary : []);

    if (modifications.length > 0) {
      summaryHtml = `
        <div class="ai-section">
          <div class="ai-section-title">Key Modifications:</div>
          <ul class="ai-summary-list">
            ${modifications.map((s) => `<li>${escapeHtml(s)}</li>`).join('')}
          </ul>
        </div>
      `;
    }

    return `
      <div class="ai-analysis-card">
        <div class="ai-card-header">
          <span class="ai-pill"><span class="ai-sparkle">✨</span> Deep Architectural Synthesis</span>
          <span class="ai-provider-badge">${escapeHtml(provider)}${cachedBadge}</span>
        </div>
        <div class="ai-section">
          <div class="ai-section-title">Architectural Intent & Rationale:</div>
          <div class="ai-intent-text">${escapeHtml(intent)}</div>
        </div>
        ${archHtml}
        <div class="ai-section">
          <div class="ai-section-title">Functionality Gained:</div>
          <div class="ai-gains-text">${escapeHtml(gains)}</div>
        </div>
        ${summaryHtml}
      </div>
    `;
  }

  function createExecAiCardHtml(ai) {
    if (!ai) return '';
    if (typeof ai === 'string') {
      try {
        ai = JSON.parse(ai);
      } catch (e) {
        return `<div class="ai-analysis-card exec-ai-card"><div class="ai-intent-text">${escapeHtml(ai)}</div></div>`;
      }
    }
    const intent = ai.intent || 'No intent description provided.';
    const techPurpose = ai.technical_purpose || 'Standard command-line execution.';
    const outcome = ai.outcome_analysis || 'Executed without errors.';
    const provider = ai.provider ? `Provider: ${ai.provider}` : 'AI Intelligence';
    const cachedBadge = ai.cached ? ' • (Cached)' : '';
    const summary = Array.isArray(ai.summary) ? ai.summary : [];

    let summaryHtml = '';
    if (summary.length > 0) {
      summaryHtml = `
        <div class="ai-section">
          <div class="ai-section-title">Key Highlights:</div>
          <ul class="ai-summary-list">
            ${summary.map((s) => `<li>${escapeHtml(s)}</li>`).join('')}
          </ul>
        </div>
      `;
    }

    return `
      <div class="ai-analysis-card exec-ai-card">
        <div class="ai-card-header">
          <span class="ai-pill"><span class="ai-sparkle">✨</span> Command Intent & Execution Analysis</span>
          <span class="ai-provider-badge">${escapeHtml(provider)}${cachedBadge}</span>
        </div>
        <div class="ai-section">
          <div class="ai-section-title">Intent & Objective:</div>
          <div class="ai-intent-text">${escapeHtml(intent)}</div>
        </div>
        <div class="ai-section">
          <div class="ai-section-title">Technical Purpose:</div>
          <div class="ai-gains-text" style="color: #38bdf8;">${escapeHtml(techPurpose)}</div>
        </div>
        <div class="ai-section">
          <div class="ai-section-title">Outcome Analysis:</div>
          <div class="ai-gains-text" style="color: #a7f3d0;">${escapeHtml(outcome)}</div>
        </div>
        ${summaryHtml}
      </div>
    `;
  }

  async function triggerExplainCommand(ev, slotEl, buttonEl) {
    state.analyzingEventIds.add(ev.id);
    if (buttonEl) {
      buttonEl.disabled = true;
      buttonEl.innerHTML = '<span class="ai-sparkle">✨</span> Explaining…';
    }
    if (slotEl) {
      slotEl.innerHTML = `
        <div class="ai-progress-track">
          <div class="ai-progress-fill"></div>
        </div>
        <div class="ai-progress-status">Explaining command intent & execution analysis…</div>
      `;
    }

    try {
      const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/analyze-exec/${ev.id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Command explanation failed');
      }

      ev.ai_summary = data.ai_summary;
      if (slotEl) {
        slotEl.innerHTML = createExecAiCardHtml(data.ai_summary);
      }
      if (buttonEl) {
        buttonEl.innerHTML = '<span class="ai-sparkle">✨</span> Re-Explain with AI';
      }
      showToast(`Command explanation complete for #${ev.id}!`, 'success');
    } catch (err) {
      console.error('Command explanation error:', err);
      showToast(`Explanation failed: ${err.message}`, 'error');
      if (slotEl) {
        slotEl.innerHTML = createExecAiCardHtml(ev.ai_summary);
      }
      if (buttonEl) {
        buttonEl.innerHTML = '<span class="ai-sparkle">✨</span> ' + (ev.ai_summary ? 'Re-Explain with AI' : 'Explain Command with AI');
      }
    } finally {
      state.analyzingEventIds.delete(ev.id);
      if (buttonEl) buttonEl.disabled = false;
    }
  }

  function renderTimeline(events, gitEvents = []) {
    if (!DOM.timelineContainer) return;
    if (!events || events.length === 0) {
      DOM.timelineContainer.dataset.signature = 'empty';
      DOM.timelineContainer.innerHTML = '<div class="empty-state">No events recorded for this project yet.</div>';
      return;
    }

    // Anti-flicker signature check: compare filter, count, event IDs, types, and whether ai_summary exists
    const signature = `${state.timelineFilter}_${events.length}_` + events.map((e) => `${e.id}_${e.event_type || 'EDIT'}_${Boolean(e.ai_summary)}_${state.analyzingEventIds.has(e.id)}`).join('|');
    if (DOM.timelineContainer.dataset.signature === signature) {
      return;
    }
    DOM.timelineContainer.dataset.signature = signature;

    const fragment = document.createDocumentFragment();

    events.forEach((ev) => {
      const type = ev.event_type || 'EDIT';

      if (type === 'EDIT') {
        const card = document.createElement('div');
        card.className = 'timeline-card';

        const entrypointFile = ev.first_file_touched || (ev.patches && ev.patches[0] ? ev.patches[0].file_path : null);
        const patchList = ev.patches || [];

        // Check for associated micro-commit
        const gitMatch = gitEvents.find((g) => g.id === ev.id + 1 || (g.summary && g.summary.includes(ev.summary)));
        let gitTagHtml = '';
        if (gitMatch && gitMatch.summary) {
          const m = gitMatch.summary.match(/Micro-commit ([a-f0-9]{7})/);
          if (m) {
            gitTagHtml = `<span class="badge" title="${escapeHtml(gitMatch.summary)}">📦 Git ${m[1]}</span>`;
          }
        }

        const fileChipsHtml = patchList
          .map((p) => {
            const isEntry = p.file_path === entrypointFile;
            return `<span class="file-chip ${isEntry ? 'entrypoint' : ''}">${escapeHtml(p.file_path)}</span>`;
          })
          .join('');

        const isAnalyzing = state.analyzingEventIds.has(ev.id);
        const aiSlotContent = isAnalyzing
          ? `<div class="ai-progress-track"><div class="ai-progress-fill"></div></div><div class="ai-progress-status">Synthesizing patch diffs & analyzing intent…</div>`
          : createAiCardHtml(ev.ai_summary);

        const burstLabelText = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
        const summaryClean = scrubSensitiveText(ev.summary || 'Multi-file edit burst');

        card.innerHTML = `
          <div class="timeline-card-header">
            <div class="timeline-badge-group">
              <span class="burst-badge">${escapeHtml(burstLabelText)}</span>
              ${gitTagHtml}
              ${entrypointFile ? `<span class="entrypoint-badge">Entrypoint: ${escapeHtml(entrypointFile)}</span>` : ''}
            </div>
            <span class="timeline-time">${escapeHtml(formatIST(ev.timestamp))}</span>
          </div>
          <div class="timeline-summary">${escapeHtml(summaryClean)}</div>
          <div class="timeline-files-list">
            ${fileChipsHtml || '<span class="empty-hint">No files touched</span>'}
          </div>
          <div class="ai-card-slot" id="ai-card-slot-${ev.id}">
            ${aiSlotContent}
          </div>
          <div class="timeline-footer">
            <span class="timeline-meta">${patchList.length} file(s) patched in this burst</span>
            <div style="display: flex; gap: 8px;">
              <button class="btn btn-danger-outline btn-sm btn-rollback-burst" data-event-id="${ev.id}" title="Rollback this burst: undo git micro-commit and move burst to trash">
                ↩ Rollback Burst
              </button>
              <button class="btn btn-ai btn-sm btn-analyze-ai" data-event-id="${ev.id}" ${isAnalyzing ? 'disabled' : ''}>
                <span class="ai-sparkle">✨</span> ${isAnalyzing ? 'Analyzing…' : (ev.ai_summary ? 'Re-Analyze with AI' : 'Analyze with AI')}
              </button>
              <button class="btn btn-secondary btn-sm btn-inspect-diff" data-event-id="${ev.id}">
                Inspect Diffs &rarr;
              </button>
            </div>
          </div>
        `;

        const btnRollback = card.querySelector('.btn-rollback-burst');
        if (btnRollback) {
          btnRollback.addEventListener('click', async () => {
            if (!confirm(`Are you sure you want to rollback and discard ${burstLabelText}? Any micro-commit will be undone and changes moved to Trash.`)) {
              return;
            }
            btnRollback.disabled = true;
            btnRollback.innerHTML = '<span class="spinner-sm"></span> Rolling back…';
            try {
              const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/rollback-burst/${ev.id}`, {
                method: 'POST',
              });
              const data = await res.json();
              if (!res.ok || !data.success) {
                throw new Error(data.message || data.error || 'Rollback failed');
              }
              btnRollback.disabled = false;
              btnRollback.innerHTML = '↩ Rollback Burst';
              showToast('✓ Burst rolled back and moved to Trash.', 'success');
              await updateTrashCount();
              await loadProjectEvents(state.selectedProject);
              await loadProjectSessions(state.selectedProject);
              await refreshProjects();
            } catch (err) {
              console.error('Rollback error:', err);
              showToast(`Rollback failed: ${err.message}`, 'error');
              btnRollback.disabled = false;
              btnRollback.innerHTML = '↩ Rollback Burst';
            }
          });
        }

        card.querySelector('.btn-inspect-diff').addEventListener('click', () => {
          selectTab('diffs');
          if (ev.session_id) {
            state.selectedSessionId = ev.session_id;
            if (DOM.diffSessionSelect) DOM.diffSessionSelect.value = String(ev.session_id);
            onDiffSessionSelected(ev.session_id, ev.id);
          } else {
            onDiffBurstSelected(ev.id);
          }
        });

        const btnAi = card.querySelector('.btn-analyze-ai');
        btnAi.addEventListener('click', async () => {
          state.analyzingEventIds.add(ev.id);
          btnAi.disabled = true;
          const originalText = btnAi.innerHTML;
          btnAi.innerHTML = '<span class="ai-sparkle">✨</span> Analyzing…';

          const slot = card.querySelector(`#ai-card-slot-${ev.id}`);
          if (slot) {
            slot.innerHTML = `
              <div class="ai-progress-track">
                <div class="ai-progress-fill"></div>
              </div>
              <div class="ai-progress-status">Synthesizing patch diffs & analyzing intent…</div>
            `;
          }

          try {
            const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/analyze-event/${ev.id}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({}),
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
              throw new Error(data.message || data.error || 'AI analysis failed');
            }

            ev.ai_summary = data.ai_summary;
            if (slot) {
              slot.innerHTML = createAiCardHtml(data.ai_summary);
            }
            btnAi.innerHTML = '<span class="ai-sparkle">✨</span> Re-Analyze with AI';
            showToast(`AI Analysis complete for Burst #${ev.id}!`, 'success');
          } catch (err) {
            console.error('AI Analysis error:', err);
            showToast(`AI Analysis failed: ${err.message}`, 'error');
            if (slot) {
              slot.innerHTML = createAiCardHtml(ev.ai_summary);
            }
            btnAi.innerHTML = originalText;
          } finally {
            state.analyzingEventIds.delete(ev.id);
            btnAi.disabled = false;
          }
        });

        fragment.appendChild(card);
      } else if (type === 'READ') {
        const card = document.createElement('div');
        card.className = 'timeline-card read-event-card';
        const fileClean = scrubSensitiveText(ev.first_file_touched || 'unknown');
        const summaryClean = scrubSensitiveText(ev.summary || 'Process inspected file handle');
        card.innerHTML = `
          <div class="timeline-card-header">
            <div class="timeline-badge-group">
              <span class="badge badge-read" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3);">👁️ READ #${ev.id}</span>
            </div>
            <span class="timeline-time">${escapeHtml(formatIST(ev.timestamp))}</span>
          </div>
          <div class="timeline-summary">Inspected file: <code class="mono" style="color: var(--accent);">${escapeHtml(fileClean)}</code></div>
          <div class="timeline-footer">
            <span class="timeline-meta">${escapeHtml(summaryClean)}</span>
          </div>
        `;
        fragment.appendChild(card);
      } else if (type === 'EXEC') {
        const isSuccess = !ev.summary.includes('failed') && !ev.summary.includes('exit: 1') && !ev.summary.includes('exit: 2');
        const badgeClass = isSuccess ? 'success' : 'failed';
        const badgeText = isSuccess ? 'SUCCESS' : 'FAILED';
        let executorBadge = '';
        if (ev.executor === 'AI_AGENT') {
          executorBadge = '<span class="badge-ai-exec" title="Automated AI Agent Execution">🤖 AI Action</span>';
        } else if (ev.executor === 'HUMAN_DEV') {
          executorBadge = '<span class="badge-human-exec" title="Interactive Developer Terminal Execution">👤 Human Dev</span>';
        }

        const cmdClean = scrubSensitiveText(ev.first_file_touched || ev.summary || '');
        const summaryClean = scrubSensitiveText(ev.summary || 'Terminal process executed');

        const isAnalyzing = state.analyzingEventIds.has(ev.id);
        const aiSlotContent = isAnalyzing
          ? `<div class="ai-progress-track"><div class="ai-progress-fill"></div></div><div class="ai-progress-status">Explaining command intent & execution analysis…</div>`
          : createExecAiCardHtml(ev.ai_summary);

        const card = document.createElement('div');
        card.className = 'timeline-card exec-event-card';
        card.innerHTML = `
          <div class="timeline-card-header">
            <div class="timeline-badge-group">
              <span class="badge badge-exec" style="background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3);">💻 EXEC #${ev.id}</span>
              ${executorBadge}
              <span class="exec-badge ${badgeClass}">${badgeText}</span>
            </div>
            <span class="timeline-time">${escapeHtml(formatIST(ev.timestamp))}</span>
          </div>
          <div class="exec-cmd-box" style="margin: 8px 0;">$ ${escapeHtml(cmdClean)}</div>
          <div class="ai-card-slot" id="ai-exec-slot-${ev.id}">
            ${aiSlotContent}
          </div>
          <div class="timeline-footer">
            <span class="timeline-meta">${escapeHtml(summaryClean)}</span>
            <button class="btn btn-ai btn-sm btn-analyze-exec-ai" data-event-id="${ev.id}" ${isAnalyzing ? 'disabled' : ''}>
              <span class="ai-sparkle">✨</span> ${isAnalyzing ? 'Explaining…' : (ev.ai_summary ? 'Re-Explain with AI' : 'Explain Command with AI')}
            </button>
          </div>
        `;

        const btnAi = card.querySelector('.btn-analyze-exec-ai');
        btnAi.addEventListener('click', async () => {
          await triggerExplainCommand(ev, card.querySelector(`#ai-exec-slot-${ev.id}`), btnAi);
        });

        fragment.appendChild(card);
      }
    });

    DOM.timelineContainer.innerHTML = '';
    DOM.timelineContainer.appendChild(fragment);
  }

  function renderAnalyzedFiles(readEvents) {
    if (!DOM.timelineReadsList) return;
    DOM.timelineReadsList.innerHTML = '';
    if (!readEvents || readEvents.length === 0) {
      DOM.timelineReadsList.innerHTML = '<div class="empty-hint">No file inspection events recorded yet</div>';
      return;
    }

    const fragment = document.createDocumentFragment();
    readEvents.slice(-50).reverse().forEach((ev) => {
      const chip = document.createElement('div');
      chip.className = 'read-chip';
      const file = scrubSensitiveText(ev.first_file_touched || 'unknown');
      const summary = scrubSensitiveText(ev.summary || '');
      chip.innerHTML = `<span>${escapeHtml(file)}</span> <span class="proc-tag">${escapeHtml(summary)}</span>`;
      fragment.appendChild(chip);
    });
    DOM.timelineReadsList.appendChild(fragment);
  }

  function renderTerminal(execEvents) {
    if (!DOM.terminalFeedContainer) return;
    DOM.terminalFeedContainer.innerHTML = '';
    if (!execEvents || execEvents.length === 0) {
      DOM.terminalFeedContainer.innerHTML = '<div class="empty-state">No terminal commands recorded yet.</div>';
      return;
    }

    const fragment = document.createDocumentFragment();
    execEvents.slice().reverse().forEach((ev) => {
      const card = document.createElement('div');
      card.className = 'exec-card';

      const isSuccess = !ev.summary.includes('failed') && !ev.summary.includes('exit: 1') && !ev.summary.includes('exit: 2');
      const badgeClass = isSuccess ? 'success' : 'failed';
      const badgeText = isSuccess ? 'SUCCESS' : 'FAILED';
      let executorBadge = '';
      if (ev.executor === 'AI_AGENT') {
        executorBadge = '<span class="badge-ai-exec" title="Automated AI Agent Execution">🤖 AI Action</span>';
      } else if (ev.executor === 'HUMAN_DEV') {
        executorBadge = '<span class="badge-human-exec" title="Interactive Developer Terminal Execution">👤 Human Dev</span>';
      }

      const cmdClean = scrubSensitiveText(ev.first_file_touched || ev.summary || '');
      const summaryClean = scrubSensitiveText(ev.summary || '');

      const isAnalyzing = state.analyzingEventIds.has(ev.id);
      const aiSlotContent = isAnalyzing
        ? `<div class="ai-progress-track"><div class="ai-progress-fill"></div></div><div class="ai-progress-status">Explaining command intent & execution analysis…</div>`
        : createExecAiCardHtml(ev.ai_summary);

      card.innerHTML = `
        <div class="exec-header">
          <div class="exec-meta-group">
            <span class="exec-badge ${badgeClass}">${badgeText}</span>
            ${executorBadge}
            <span class="timeline-meta">${escapeHtml(summaryClean)}</span>
          </div>
          <span class="timeline-time">${escapeHtml(formatIST(ev.timestamp))}</span>
        </div>
        <div class="exec-cmd-box">$ ${escapeHtml(cmdClean)}</div>
        <div class="ai-card-slot" id="ai-term-slot-${ev.id}">
          ${aiSlotContent}
        </div>
        <div class="exec-footer">
          <span>Command Execution #${ev.id} • Logged in session.db</span>
          <button class="btn btn-ai btn-sm btn-analyze-term-ai" data-event-id="${ev.id}" ${isAnalyzing ? 'disabled' : ''}>
            <span class="ai-sparkle">✨</span> ${isAnalyzing ? 'Explaining…' : (ev.ai_summary ? 'Re-Explain with AI' : 'Explain Command with AI')}
          </button>
        </div>
      `;

      const btnAi = card.querySelector('.btn-analyze-term-ai');
      btnAi.addEventListener('click', async () => {
        await triggerExplainCommand(ev, card.querySelector(`#ai-term-slot-${ev.id}`), btnAi);
      });

      fragment.appendChild(card);
    });
    DOM.terminalFeedContainer.appendChild(fragment);
  }

  function renderDashboardRecentEvents(recentEvents) {
    DOM.dashRecentEvents.innerHTML = '';
    const events = recentEvents || state.eventsData;
    if (!events || events.length === 0) {
      DOM.dashRecentEvents.innerHTML = '<div class="empty-state">No events recorded yet.</div>';
      return;
    }

    const recent = events.slice(-3).reverse();
    recent.forEach((ev) => {
      const row = document.createElement('div');
      row.className = 'compact-event-row';
      const burstLabel = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
      const summaryClean = scrubSensitiveText(ev.summary || '');
      row.innerHTML = `
        <div>
          <strong>${escapeHtml(burstLabel)}</strong> — ${escapeHtml(summaryClean)}
        </div>
        <span class="timeline-time">${escapeHtml(formatIST(ev.timestamp))}</span>
      `;
      DOM.dashRecentEvents.appendChild(row);
    });
  }

  // ---------------------------------------------------------------------------
  // Diff Viewer Management (3-Tier Hierarchical Navigation)
  // ---------------------------------------------------------------------------

  function renderSessionDropdown(sessions) {
    if (!DOM.diffSessionSelect) return;
    DOM.diffSessionSelect.innerHTML = '';
    if (!sessions || sessions.length === 0) {
      DOM.diffSessionSelect.innerHTML = '<option value="">(No sessions recorded)</option>';
      return;
    }
    sessions.forEach((s, idx) => {
      const sessId = s.session_id ?? s.id ?? (idx + 1);
      const bCount = s.burst_count ?? s.bursts_count ?? 0;
      const opt = document.createElement('option');
      opt.value = String(sessId);
      const label = `Session ${sessId}` + (s.is_active ? ' (Active)' : ` (${bCount} bursts)`);
      opt.textContent = label;
      DOM.diffSessionSelect.appendChild(opt);
    });
  }

  function setupDiffViewerHierarchy(editEvents) {
    if (!DOM.diffSessionSelect) return;
    DOM.diffSessionSelect.innerHTML = '';
    if (DOM.diffBurstSelect) DOM.diffBurstSelect.innerHTML = '';

    let events = editEvents;
    if (!events || events.length === 0) {
      events = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
    }

    if (!events || events.length === 0) {
      renderSessionDropdown([]);
      if (DOM.diffBurstSelect) DOM.diffBurstSelect.innerHTML = '<option value="">(No edit bursts available)</option>';
      if (DOM.diffBurstCount) DOM.diffBurstCount.textContent = '0';
      if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
      if (DOM.diffFilesList) DOM.diffFilesList.innerHTML = '<div class="empty-hint">No edit bursts</div>';
      if (DOM.diffCodeContainer) DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No edit events available to inspect for this project.</div>';
      if (DOM.diffAiSlot) DOM.diffAiSlot.innerHTML = '';
      return;
    }

    // Determine sessions: use state.sessionsData if available, otherwise aggregate from events
    let sessions = (state.sessionsData && state.sessionsData.length > 0) ? state.sessionsData.slice() : null;
    if (!sessions || sessions.length === 0) {
      const sMap = new Map();
      events.forEach((ev) => {
        const sid = ev.session_id || 1;
        if (!sMap.has(sid)) {
          sMap.set(sid, { session_id: sid, id: sid, is_active: false, burst_count: 0 });
        }
        sMap.get(sid).burst_count += 1;
      });
      sessions = Array.from(sMap.values());
    }

    // Filter out empty sessions: do not return sessions where burst_count == 0 unless active
    sessions = sessions.filter((s) => s.is_active || (s.burst_count ?? s.bursts_count ?? 0) > 0);

    if (sessions.length === 0) {
      renderSessionDropdown([]);
      if (DOM.diffBurstSelect) DOM.diffBurstSelect.innerHTML = '<option value="">(No edit bursts available)</option>';
      if (DOM.diffBurstCount) DOM.diffBurstCount.textContent = '0';
      if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
      if (DOM.diffFilesList) DOM.diffFilesList.innerHTML = '<div class="empty-hint">No edit bursts</div>';
      if (DOM.diffCodeContainer) DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No edit events available to inspect for this project.</div>';
      if (DOM.diffAiSlot) DOM.diffAiSlot.innerHTML = '';
      return;
    }

    // Sort sessions ascending by session_id
    sessions.sort((a, b) => ((a.session_id ?? a.id ?? 1) - (b.session_id ?? b.id ?? 1)));

    // Populate Level 1 (Session Selector) using safe label resolution
    renderSessionDropdown(sessions);

    // Select target session: prioritize current state.selectedSessionId if valid, otherwise latest
    let targetSessionId = null;
    if (state.selectedSessionId && sessions.some((s) => (s.session_id ?? s.id) === state.selectedSessionId)) {
      targetSessionId = state.selectedSessionId;
    } else {
      const lastS = sessions[sessions.length - 1];
      targetSessionId = lastS.session_id ?? lastS.id;
    }

    DOM.diffSessionSelect.value = String(targetSessionId);
    onDiffSessionSelected(targetSessionId);
  }

  function onDiffSessionSelected(sessionId, preferredEventId) {
    const sid = parseInt(sessionId, 10);
    state.selectedSessionId = isNaN(sid) ? null : sid;

    const allEditEvents = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
    const sessionBursts = allEditEvents.filter((ev) => (ev.session_id || 1) === (state.selectedSessionId || 1));

    if (DOM.diffBurstCount) DOM.diffBurstCount.textContent = sessionBursts.length;

    if (!DOM.diffBurstSelect) return;
    DOM.diffBurstSelect.innerHTML = '';

    if (sessionBursts.length === 0) {
      DOM.diffBurstSelect.innerHTML = '<option value="">(No bursts in this session)</option>';
      if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
      if (DOM.diffFilesList) DOM.diffFilesList.innerHTML = '<div class="empty-hint">No edit bursts</div>';
      if (DOM.diffCodeContainer) DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No file diffs recorded for this session.</div>';
      if (DOM.diffAiSlot) DOM.diffAiSlot.innerHTML = '';
      return;
    }

    sessionBursts.forEach((ev) => {
      const opt = document.createElement('option');
      opt.value = ev.id;
      const patchCount = ev.patches ? ev.patches.length : 0;
      const timeStr = formatISTTime(ev.timestamp);
      const commitTag = ev.commit_hash ? ` [${ev.commit_hash.slice(0, 7)}]` : '';
      const file = ev.first_file_touched || 'edit';
      const burstLabel = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
      opt.textContent = `${burstLabel} • ${timeStr} [${patchCount} file(s)]${commitTag} — ${file}`;
      DOM.diffBurstSelect.appendChild(opt);
    });

    let targetEventId = null;
    if (preferredEventId && sessionBursts.some((ev) => ev.id === preferredEventId)) {
      targetEventId = preferredEventId;
    } else if (state.selectedEventId && sessionBursts.some((ev) => ev.id === state.selectedEventId)) {
      targetEventId = state.selectedEventId;
    } else {
      targetEventId = sessionBursts[sessionBursts.length - 1].id;
    }

    DOM.diffBurstSelect.value = String(targetEventId);
    onDiffBurstSelected(targetEventId);
  }

  function onDiffBurstSelected(eventId) {
    state.selectedEventId = parseInt(eventId, 10);
    const ev = state.eventsData.find((x) => x.id === state.selectedEventId);

    // Sync Level 1 session dropdown if event has a different session_id
    if (ev && ev.session_id && ev.session_id !== state.selectedSessionId) {
      state.selectedSessionId = ev.session_id;
      if (DOM.diffSessionSelect) DOM.diffSessionSelect.value = String(ev.session_id);
    }

    // Step 3 Header: Render AI Intent & Summary Banner
    if (DOM.diffAiSlot) {
      if (ev && ev.ai_summary) {
        DOM.diffAiSlot.innerHTML = createAiCardHtml(ev.ai_summary);
      } else if (ev) {
        const burstLabel = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
        DOM.diffAiSlot.innerHTML = `
          <div class="diff-ai-banner-empty">
            <div class="diff-ai-banner-left">
              <span class="ai-sparkle">✨</span>
              <span>No AI architectural synthesis generated for ${escapeHtml(burstLabel)} yet.</span>
            </div>
            <button class="btn btn-secondary btn-xs btn-diff-analyze" id="btn-diff-analyze-${ev.id}">
              Analyze ${escapeHtml(burstLabel)}
            </button>
          </div>
        `;
        const analyzeBtn = document.getElementById(`btn-diff-analyze-${ev.id}`);
        if (analyzeBtn) {
          analyzeBtn.addEventListener('click', async () => {
            analyzeBtn.disabled = true;
            analyzeBtn.textContent = 'Analyzing…';
            try {
              const res = await apiPost(`/api/project/${encodeURIComponent(state.selectedProject)}/events/${ev.id}/analyze`, {});
              if (res.ai_summary) {
                ev.ai_summary = res.ai_summary;
                DOM.diffAiSlot.innerHTML = createAiCardHtml(ev.ai_summary);
                renderTimelineFromState();
              }
            } catch (err) {
              showToast(`Analysis failed: ${err.message}`, 'error');
              analyzeBtn.disabled = false;
              analyzeBtn.textContent = `Analyze ${burstLabel}`;
            }
          });
        }
      } else {
        DOM.diffAiSlot.innerHTML = '';
      }
    }

    if (!ev || !ev.patches || ev.patches.length === 0) {
      state.selectedFilePath = null;
      if (DOM.diffFileCount) DOM.diffFileCount.textContent = '0';
      DOM.diffFilesList.innerHTML = '<div class="empty-hint">No patches for this event</div>';
      DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No file diffs recorded for this burst.</div>';
      DOM.diffCurrentFileName.textContent = 'None';
      DOM.statAdditions.textContent = '+0';
      DOM.statDeletions.textContent = '-0';
      return;
    }

    if (DOM.diffFileCount) DOM.diffFileCount.textContent = ev.patches.length;
    DOM.diffFilesList.innerHTML = '';

    // Check if previously selected file exists in this event's patches
    let targetPatch = ev.patches[0];
    if (state.selectedFilePath) {
      const found = ev.patches.find((p) => p.file_path === state.selectedFilePath);
      if (found) targetPatch = found;
    }

    ev.patches.forEach((p) => {
      const btn = document.createElement('button');
      btn.className = `diff-file-item ${p === targetPatch ? 'active' : ''}`;

      // Calculate lines added/deleted if not already present
      let adds = typeof p.lines_added === 'number' ? p.lines_added : 0;
      let dels = typeof p.lines_deleted === 'number' ? p.lines_deleted : 0;
      if (!p.lines_added && !p.lines_deleted && p.diff_content) {
        p.diff_content.split('\n').forEach((l) => {
          if (l.startsWith('+') && !l.startsWith('+++')) adds++;
          else if (l.startsWith('-') && !l.startsWith('---')) dels++;
        });
        p.lines_added = adds;
        p.lines_deleted = dels;
      }

      btn.innerHTML = `
        <span class="diff-file-name" title="${escapeHtml(p.file_path)}">📄 ${escapeHtml(p.file_path)}</span>
        <span class="diff-file-stats">
          ${adds > 0 ? `<span class="stat-badge-add">+${adds}</span>` : ''}
          ${dels > 0 ? `<span class="stat-badge-del">-${dels}</span>` : ''}
        </span>
      `;

      btn.addEventListener('click', () => {
        state.selectedFilePath = p.file_path;
        document.querySelectorAll('.diff-file-item').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        renderPatchDiff(p);
      });
      DOM.diffFilesList.appendChild(btn);
    });

    state.selectedFilePath = targetPatch.file_path;
    // Render the target patch
    renderPatchDiff(targetPatch);
  }

  // Backwards compatibility aliases
  function setupDiffEventDropdown(editEvents) {
    setupDiffViewerHierarchy(editEvents);
  }

  function onDiffEventSelected(eventId) {
    onDiffBurstSelected(eventId);
  }

  function renderPatchDiff(patch) {
    if (!patch) return;
    DOM.diffCurrentFileName.textContent = patch.file_path;
    const diffContent = patch.diff_content || '';

    if (!diffContent.trim()) {
      DOM.diffCodeContainer.innerHTML = '<div class="empty-state">No text diff content recorded for this file.</div>';
      DOM.statAdditions.textContent = '+0';
      DOM.statDeletions.textContent = '-0';
      return;
    }

    let additions = 0;
    let deletions = 0;
    const lines = diffContent.split('\n');
    const fragment = document.createDocumentFragment();

    lines.forEach((line) => {
      const row = document.createElement('div');
      row.className = 'diff-line';

      if (line.startsWith('---') || line.startsWith('+++')) {
        row.className += ' diff-file-header';
        row.innerHTML = `<span class="diff-line-content">${escapeHtml(line)}</span>`;
      } else if (line.startsWith('@@')) {
        row.className += ' diff-hunk';
        row.innerHTML = `<span class="diff-line-content">${escapeHtml(line)}</span>`;
      } else if (line.startsWith('+')) {
        additions++;
        row.className += ' diff-add';
        row.innerHTML = `<span class="diff-line-content">${escapeHtml(line)}</span>`;
      } else if (line.startsWith('-')) {
        deletions++;
        row.className += ' diff-del';
        row.innerHTML = `<span class="diff-line-content">${escapeHtml(line)}</span>`;
      } else {
        row.innerHTML = `<span class="diff-line-content">${escapeHtml(line)}</span>`;
      }

      fragment.appendChild(row);
    });

    DOM.statAdditions.textContent = `+${additions}`;
    DOM.statDeletions.textContent = `-${deletions}`;

    DOM.diffCodeContainer.innerHTML = '';
    DOM.diffCodeContainer.appendChild(fragment);
  }

  // ---------------------------------------------------------------------------
  // Logs Management
  // ---------------------------------------------------------------------------

  async function loadProjectLogs(projectName) {
    if (!projectName) return;
    try {
      const data = await apiGet(`/api/project/${encodeURIComponent(projectName)}/logs`);
      const lines = data.lines || [];

      if (lines.length === 0) {
        DOM.logsTerminal.innerHTML = '<div class="log-line">No log lines recorded yet.</div>';
        return;
      }

      const fragment = document.createDocumentFragment();
      lines.forEach((raw) => {
        const lineDiv = document.createElement('div');
        lineDiv.className = 'log-line';

        if (raw.includes('ERROR') || raw.includes('CRITICAL')) {
          lineDiv.className += ' err';
        } else if (raw.includes('WARNING') || raw.includes('WARN')) {
          lineDiv.className += ' warn';
        } else if (raw.startsWith('{') && raw.endsWith('}')) {
          lineDiv.className += ' json';
        } else if (raw.includes('INFO')) {
          lineDiv.className += ' info';
        }

        lineDiv.textContent = raw;
        fragment.appendChild(lineDiv);
      });

      DOM.logsTerminal.innerHTML = '';
      DOM.logsTerminal.appendChild(fragment);

      if (state.autoScrollLogs) {
        DOM.logsTerminal.scrollTop = DOM.logsTerminal.scrollHeight;
      }
    } catch (err) {
      DOM.logsTerminal.innerHTML = `<div class="log-line err">Error loading logs: ${escapeHtml(err.message)}</div>`;
    }
  }

  // ---------------------------------------------------------------------------
  // Real-time Streaming (SSE with Fallback Polling)
  // ---------------------------------------------------------------------------

  function initSSE() {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }

    try {
      const es = new EventSource('/api/stream');
      state.eventSource = es;

      es.addEventListener('open', () => {
        state.sseActive = true;
        DOM.connStatus.querySelector('.status-dot').className = 'status-dot connected';
        DOM.connText.textContent = 'SSE Stream Active';
      });

      es.addEventListener('connected', (e) => {
        state.sseActive = true;
      });

      es.addEventListener('ping', (e) => {
        try {
          const payload = JSON.parse(e.data);
          handleLivePing(payload);
        } catch (_) {}
      });

      es.onerror = () => {
        state.sseActive = false;
        DOM.connStatus.querySelector('.status-dot').className = 'status-dot disconnected';
        DOM.connText.textContent = 'Reconnecting / Polling…';
        es.close();
        state.eventSource = null;

        // Fallback to polling
        if (!state.pollTimer) {
          state.pollTimer = setInterval(pollLiveStatus, 1500);
        }
      };
    } catch (_) {
      // Direct fallback to polling if EventSource is not supported
      if (!state.pollTimer) {
        state.pollTimer = setInterval(pollLiveStatus, 1500);
      }
    }
  }

  function renderLiveTicker(actions) {
    if (!DOM.liveTickerFeed) return;
    if (!actions || actions.length === 0) {
      if (DOM.liveActionCount) DOM.liveActionCount.textContent = '0 actions';
      DOM.liveTickerFeed.innerHTML = '<div class="ticker-empty">Listening for file modifications, file inspections, and executions…</div>';
      return;
    }

    if (DOM.liveActionCount) {
      DOM.liveActionCount.textContent = `${actions.length} action${actions.length === 1 ? '' : 's'}`;
    }

    // Check signature to avoid DOM churn
    const sig = actions.map((a) => `${a.timestamp || a.iso || a.time}_${a.action_type || a.type}_${a.file_path || a.file}`).join('|');
    if (DOM.liveTickerFeed.dataset.signature === sig) {
      return;
    }
    DOM.liveTickerFeed.dataset.signature = sig;

    const fragment = document.createDocumentFragment();
    actions.slice().reverse().forEach((action) => {
      const row = document.createElement('div');
      row.className = 'ticker-row';

      const type = action.action_type || action.type || 'WRITE';
      let typeClass = 'write';
      let icon = '✏️';
      if (type === 'READ') {
        typeClass = 'read';
        icon = '👁️';
      } else if (type === 'EXEC') {
        typeClass = 'exec';
        icon = '💻';
      } else if (type === 'BURST') {
        typeClass = 'burst';
        icon = '⚡';
      }

      const rawTime = action.timestamp || action.iso || action.time || '';
      const timeStr = formatISTTime(rawTime);
      const file = action.file_path || action.file || '';
      const details = action.details ? ` (${action.details})` : '';

      row.innerHTML = `
        <span class="ticker-badge ${typeClass}">${icon} ${escapeHtml(type)}</span>
        <span class="ticker-time">[${escapeHtml(timeStr)}]</span>
        <span class="ticker-path mono" title="${escapeHtml(file)}">${escapeHtml(file)}${escapeHtml(details)}</span>
      `;
      fragment.appendChild(row);
    });

    DOM.liveTickerFeed.innerHTML = '';
    DOM.liveTickerFeed.appendChild(fragment);
  }

  async function handleResumeProject() {
    const p = getSelectedProjectInfo();
    if (!p) {
      showToast('Please select a project to resume.', 'error');
      return;
    }

    if (p.is_active) {
      showToast(`Project '${p.name}' is already actively monitored!`, 'info');
      return;
    }

    if (DOM.btnHeaderResume) DOM.btnHeaderResume.disabled = true;
    if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.disabled = true;
    showToast(`Resuming monitoring for '${p.name}'…`, 'info');

    try {
      const res = await fetch(`/api/project/${encodeURIComponent(p.name)}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Failed to resume project');
      }

      showToast(`Resumed monitoring for '${p.name}' (PID: ${data.result ? data.result.pid : ''})`, 'success');
      await refreshProjects();
      selectProject(p.name, 'current');
    } catch (err) {
      console.error('Resume project error:', err);
      showToast(`Failed to resume: ${err.message}`, 'error');
    } finally {
      if (DOM.btnHeaderResume) DOM.btnHeaderResume.disabled = false;
      if (DOM.btnTelemetryResume) DOM.btnTelemetryResume.disabled = false;
    }
  }

  function handleLivePing(payload) {
    if (!payload || !payload.workers) return;
    const workers = payload.workers;

    // Check if active project status changed or if any worker heartbeat updated
    const activeProject = getSelectedProjectInfo();
    if (activeProject && state.selectedTier === 'current') {
      const match = workers.find((w) => w.project === activeProject.name);
      if (match) {
        activeProject.is_active = match.alive;
        activeProject.uptime_s = match.uptime_s;
        activeProject.memory_mb = match.memory_mb;
        activeProject.pid = match.pid;
        activeProject.session_id = match.session_id;
        if (match.debounce) activeProject.debounce = match.debounce;
        if (Array.isArray(match.recent_actions)) {
          activeProject.recent_actions = match.recent_actions;
          renderLiveTicker(match.recent_actions);
        }
        updateHeaderTelemetry();

        // Refresh events if count might have changed
        if (state.activeTab === 'dashboard' || state.activeTab === 'timeline' || state.activeTab === 'diffs') {
          loadProjectEvents(activeProject.name);
        }
        if (state.activeTab === 'logs') {
          loadProjectLogs(activeProject.name);
        }
      }
    }
  }

  async function pollLiveStatus() {
    try {
      const statusData = await apiGet('/api/status');
      handleLivePing(statusData);
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // Modal & Project Actions
  // ---------------------------------------------------------------------------

  function openNewSessionModal() {
    DOM.formNewSession.reset();
    DOM.slugPreview.textContent = 'my_project';
    DOM.debounceValDisplay.innerHTML = '3.5s <span class="badge badge-recommended">Recommended</span>';
    DOM.rngDebounce.value = '3.5';
    if (DOM.selIdeProfile) DOM.selIdeProfile.value = 'antigravity';
    if (DOM.groupCustomIdeMarker) DOM.groupCustomIdeMarker.classList.add('hidden');
    if (DOM.inpCustomIdeMarker) DOM.inpCustomIdeMarker.value = '';
    DOM.modalError.classList.add('hidden');
    DOM.modalSpinner.classList.add('hidden');
    DOM.modalSubmitBtn.disabled = false;
    DOM.modalNewSession.classList.add('open');
  }

  function closeNewSessionModal() {
    DOM.modalNewSession.classList.remove('open');
  }

  async function handleLaunchNewSession(e) {
    e.preventDefault();
    const name = DOM.inpProjectName.value.trim();
    const targetPath = DOM.inpTargetPath.value.trim();
    const scaffold = DOM.chkScaffold.checked;
    const debounce = parseFloat(DOM.rngDebounce.value);

    if (!name || !targetPath) {
      DOM.modalError.textContent = 'Please fill in both project name and workspace path.';
      DOM.modalError.classList.remove('hidden');
      return;
    }

    DOM.modalError.classList.add('hidden');
    DOM.modalSpinner.classList.remove('hidden');
    DOM.modalSubmitBtn.disabled = true;

    try {
      const trackReads = DOM.chkPowerReads ? DOM.chkPowerReads.checked : true;
      const trackExec = DOM.chkPowerExec ? DOM.chkPowerExec.checked : true;
      const shadowGit = DOM.chkPowerShadowGit ? DOM.chkPowerShadowGit.checked : true;
      const gitInitPrimary = DOM.chkPowerGitInit ? DOM.chkPowerGitInit.checked : false;
      const ideProfile = DOM.selIdeProfile ? DOM.selIdeProfile.value : 'antigravity';
      const ideCustomMarker = (DOM.selIdeProfile && DOM.selIdeProfile.value === 'custom' && DOM.inpCustomIdeMarker)
        ? DOM.inpCustomIdeMarker.value.trim()
        : null;

      const res = await apiPost('/api/projects/start', {
        project: name,
        path: targetPath,
        scaffold: scaffold,
        debounce: debounce,
        track_reads: trackReads,
        track_exec: trackExec,
        shadow_git: shadowGit,
        git_init_primary: gitInitPrimary,
        ide_profile: ideProfile,
        ide_custom_marker: ideCustomMarker,
      });

      closeNewSessionModal();
      showToast(`Worker started for '${name}' (PID ${res.result ? res.result.pid : ''})`, 'success');
      await refreshProjects();
      selectProject(name, 'current');
    } catch (err) {
      DOM.modalError.textContent = `Launch failed: ${err.message}`;
      DOM.modalError.classList.remove('hidden');
    } finally {
      DOM.modalSpinner.classList.add('hidden');
      DOM.modalSubmitBtn.disabled = false;
    }
  }

  async function handleStopActiveProject() {
    const p = getSelectedProjectInfo();
    if (!p || !p.is_active) return;

    if (!confirm(`Are you sure you want to stop monitoring '${p.name}' and archive this session?`)) {
      return;
    }

    try {
      await apiPost('/api/projects/stop', { project: p.name });
      showToast(`Project '${p.name}' stopped and archived.`, 'success');
      await refreshProjects();
      selectProject(p.name, 'last_run');
    } catch (err) {
      showToast(`Stop failed: ${err.message}`, 'error');
    }
  }

  function handleExportSession() {
    if (!state.selectedProject) return;
    const url = `/api/project/${encodeURIComponent(state.selectedProject)}/export`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `spd_session_${state.selectedProject}_${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    showToast('Session export JSON downloaded.', 'success');
  }

  // ---------------------------------------------------------------------------
  // GitHub Settings & Milestone Sync Review
  // ---------------------------------------------------------------------------

  async function openGitSettingsModal() {
    if (!DOM.modalGitSettings) return;
    if (DOM.gitTestStatus) {
      DOM.gitTestStatus.className = 'git-test-badge hidden';
      DOM.gitTestStatus.textContent = '';
    }
    DOM.modalGitSettings.classList.add('open');

    try {
      const res = await fetch('/api/git/config');
      const data = await res.json();
      if (data.success && data.config) {
        const c = data.config;
        DOM.inpGitUsername.value = c.github_username || '';
        DOM.inpGitToken.value = c.github_token || '';
        DOM.inpGitDefaultRemote.value = c.default_remote || 'origin';
        DOM.inpGitDefaultBranch.value = c.default_branch || 'main';
        DOM.chkGitAutoChangelog.checked = c.auto_generate_changelog !== false;
        if (!DOM.inpGitTestUrl.value) {
          DOM.inpGitTestUrl.value = c.default_remote && c.default_remote.startsWith('http') ? c.default_remote : '';
        }
      }
    } catch (err) {
      console.warn('Failed to load git config:', err);
    }
  }

  function closeGitSettingsModal() {
    if (DOM.modalGitSettings) DOM.modalGitSettings.classList.remove('open');
  }

  async function handleSaveGitSettings(e) {
    if (e) e.preventDefault();
    const payload = {
      github_username: DOM.inpGitUsername.value.trim(),
      github_token: DOM.inpGitToken.value.trim(),
      default_remote: DOM.inpGitDefaultRemote.value.trim() || 'origin',
      default_branch: DOM.inpGitDefaultBranch.value.trim() || 'main',
      auto_generate_changelog: DOM.chkGitAutoChangelog.checked,
    };

    try {
      const res = await fetch('/api/git/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Failed to save settings');
      }
      showToast('GitHub settings saved successfully.', 'success');
      closeGitSettingsModal();
    } catch (err) {
      showToast(`Save failed: ${err.message}`, 'error');
    }
  }

  async function handleTestGitConnection() {
    const testUrl = DOM.inpGitTestUrl.value.trim() || DOM.inpGitDefaultRemote.value.trim();
    const user = DOM.inpGitUsername.value.trim();
    const token = DOM.inpGitToken.value.trim();

    DOM.gitTestStatus.className = 'git-test-badge testing';
    DOM.gitTestStatus.textContent = 'Testing connection via git ls-remote…';
    DOM.btnTestGitConnection.disabled = true;

    try {
      const res = await fetch('/api/git/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo_url: testUrl,
          github_username: user,
          github_token: token,
        }),
      });
      const data = await res.json();
      if (data.success) {
        DOM.gitTestStatus.className = 'git-test-badge success';
        DOM.gitTestStatus.textContent = `✔ ${data.message || 'Connection successful!'}`;
      } else {
        DOM.gitTestStatus.className = 'git-test-badge error';
        DOM.gitTestStatus.textContent = `✖ ${data.message || 'Connection failed.'}`;
      }
    } catch (err) {
      DOM.gitTestStatus.className = 'git-test-badge error';
      DOM.gitTestStatus.textContent = `✖ Error: ${err.message}`;
    } finally {
      DOM.btnTestGitConnection.disabled = false;
    }
  }

  async function handleOpenSyncReview() {
    if (!state.selectedProject) {
      showToast('Please select a project first.', 'error');
      return;
    }

    if (DOM.btnHeaderPushGit) DOM.btnHeaderPushGit.disabled = true;
    showToast('Synthesizing Conventional Commit & Changelog…', 'info');

    try {
      const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/prepare-sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Failed to prepare sync');
      }

      DOM.inpSyncCommitTitle.value = data.commit_title || 'feat: milestone sync';
      DOM.inpSyncCommitBody.value = data.commit_body || '';
      DOM.inpSyncChangelogContent.value = data.changelog_entry || '';
      DOM.inpSyncRemote.value = data.remote_url || data.remote || 'origin';
      DOM.inpSyncBranch.value = data.branch || 'main';
      DOM.syncEventsCount.textContent = data.events_count || 0;
      DOM.syncProviderBadge.textContent = (data.provider || 'AI').toUpperCase();

      DOM.syncError.classList.add('hidden');
      DOM.syncError.textContent = '';
      DOM.modalSyncReview.classList.add('open');
    } catch (err) {
      console.error('Prepare sync error:', err);
      showToast(`Prepare sync failed: ${err.message}`, 'error');
    } finally {
      if (DOM.btnHeaderPushGit) DOM.btnHeaderPushGit.disabled = false;
    }
  }

  function closeSyncReviewModal() {
    if (DOM.modalSyncReview) DOM.modalSyncReview.classList.remove('open');
  }

  async function handleConfirmSyncPush() {
    if (!state.selectedProject) return;

    const commitTitle = DOM.inpSyncCommitTitle.value.trim();
    if (!commitTitle) {
      DOM.syncError.textContent = 'Commit title is required.';
      DOM.syncError.classList.remove('hidden');
      return;
    }

    const commitBody = DOM.inpSyncCommitBody.value.trim();
    const fullCommitMsg = commitBody ? `${commitTitle}\n\n${commitBody}` : commitTitle;
    const changelogContent = DOM.inpSyncChangelogContent.value.trim();
    let remote = DOM.inpSyncRemote.value.trim() || 'origin';
    const branch = DOM.inpSyncBranch.value.trim() || 'main';
    const autoCreate = DOM.chkSyncAutoCreateRepo ? DOM.chkSyncAutoCreateRepo.checked : false;

    DOM.syncError.classList.add('hidden');
    DOM.btnConfirmSyncPush.disabled = true;
    DOM.syncSpinner.classList.remove('hidden');

    function updateStepper(step, pct, msg) {
      if (DOM.syncStepperContainer) DOM.syncStepperContainer.classList.remove('hidden');
      if (DOM.syncStepperFill) DOM.syncStepperFill.style.width = `${pct}%`;
      if (DOM.syncStepperMsg) DOM.syncStepperMsg.textContent = msg;
      [DOM.step1, DOM.step2, DOM.step3].forEach((el, idx) => {
        if (!el) return;
        const num = idx + 1;
        el.classList.remove('active', 'done');
        if (num < step) el.classList.add('done');
        else if (num === step) el.classList.add('active');
      });
    }

    try {
      // Step 1: Synthesize & verify changelog
      updateStepper(1, 33, '1/3 Generating semantic changelog & commit structure…');
      await new Promise((r) => setTimeout(r, 350));

      if (autoCreate) {
        // Step 2: Remote verification / creation
        updateStepper(2, 66, '2/3 Verifying remote repository on GitHub…');

        let repoName = state.selectedProject;
        if (remote.startsWith('http://') || remote.startsWith('https://')) {
          const parts = remote.replace(/\.git$/, '').split('/');
          repoName = parts[parts.length - 1] || state.selectedProject;
        }

        const createRes = await fetch('/api/git/create-repo', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_name: state.selectedProject,
            repo_name: repoName,
            private: false,
          }),
        });
        const createData = await createRes.json();
        if (!createRes.ok || !createData.success) {
          throw new Error(createData.message || createData.error || 'Failed to auto-create GitHub repository');
        }
        if (createData.clone_url) {
          remote = createData.clone_url;
          DOM.inpSyncRemote.value = createData.clone_url;
        }
        if (createData.created) {
          showToast(`Repository '${repoName}' created on GitHub!`, 'info');
        }
      } else {
        updateStepper(2, 66, '2/3 Remote repository verified…');
        await new Promise((r) => setTimeout(r, 250));
      }

      // Step 3: Staging, committing & pushing
      updateStepper(3, 90, '3/3 Staging, committing & pushing to GitHub…');

      const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/git-push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          remote_url: remote,
          remote: remote,
          branch: branch,
          commit_message: fullCommitMsg,
          changelog_content: changelogContent,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Git push failed');
      }

      updateStepper(3, 100, '✔ Pushed to GitHub successfully!');
      showToast(data.message || `Successfully pushed to ${remote}/${branch}!`, 'success');
      setTimeout(async () => {
        closeSyncReviewModal();
        await refreshProjects();
        loadProjectSpecs(state.selectedProject);
      }, 700);
    } catch (err) {
      console.error('Git push error:', err);
      DOM.syncError.textContent = `Git push failed: ${err.message}`;
      DOM.syncError.classList.remove('hidden');
      showToast(`Git push failed: ${err.message}`, 'error');
    } finally {
      DOM.btnConfirmSyncPush.disabled = false;
      DOM.syncSpinner.classList.add('hidden');
    }
  }

  // ---------------------------------------------------------------------------
  // Batch AI Analysis
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // Async Batch AI Analysis & Global Floating Job Tray
  // ---------------------------------------------------------------------------

  let batchPollInterval = null;

  function updateGlobalTray(projectName, analyzed, total, status, errorMsg, isCoolingDown = false, cooldownRemaining = 0) {
    if (!DOM.globalJobTray) return;
    DOM.globalJobTray.classList.remove('hidden');

    if (DOM.trayProjectName) {
      DOM.trayProjectName.textContent = `Project: ${projectName || state.selectedProject || 'Workspace'}`;
    }

    const pct = total > 0 ? Math.min(100, Math.round((analyzed / total) * 100)) : 0;
    if (DOM.trayProgressBar) DOM.trayProgressBar.style.width = `${pct}%`;
    if (DOM.trayPercent) DOM.trayPercent.textContent = `${pct}%`;
    if (DOM.trayCounts) DOM.trayCounts.textContent = `Analyzed ${analyzed} / ${total} bursts`;

    if (isCoolingDown && cooldownRemaining > 0) {
      if (DOM.trayCooldownBadge) DOM.trayCooldownBadge.classList.remove('hidden');
      if (DOM.trayCooldownText) DOM.trayCooldownText.textContent = `Rate-Limit Cooldown: ${Math.round(cooldownRemaining)}s remaining`;
    } else {
      if (DOM.trayCooldownBadge) DOM.trayCooldownBadge.classList.add('hidden');
    }

    if (status === 'completed') {
      if (DOM.trayTitle) DOM.trayTitle.textContent = 'Batch Analysis Complete ✔';
      if (DOM.traySpinner) DOM.traySpinner.style.display = 'none';
      setTimeout(() => {
        if (DOM.globalJobTray) DOM.globalJobTray.classList.add('hidden');
      }, 6000);
    } else if (status === 'failed') {
      if (DOM.trayTitle) DOM.trayTitle.textContent = 'Batch Analysis Error ⚠️';
      if (DOM.traySpinner) DOM.traySpinner.style.display = 'none';
    } else {
      if (DOM.trayTitle) DOM.trayTitle.textContent = isCoolingDown ? 'AI Queue Cooling Down ⏳' : 'AI Batch Synthesis Active';
      if (DOM.traySpinner) DOM.traySpinner.style.display = 'inline-block';
    }
  }

  function hideGlobalTray() {
    if (DOM.globalJobTray) DOM.globalJobTray.classList.add('hidden');
  }

  if (DOM.trayCloseBtn) {
    DOM.trayCloseBtn.onclick = hideGlobalTray;
  }

  function showBatchProgress(analyzed, total, status, errorMsg, isCoolingDown = false, cooldownRemaining = 0) {
    if (!DOM.batchProgressContainer) return;
    DOM.batchProgressContainer.style.display = 'block';

    const pct = total > 0 ? Math.min(100, Math.round((analyzed / total) * 100)) : 0;
    if (DOM.batchProgressFill) DOM.batchProgressFill.style.width = `${pct}%`;
    if (DOM.batchProgressPct) DOM.batchProgressPct.textContent = `${pct}%`;

    if (DOM.batchSpinner) {
      DOM.batchSpinner.style.display = status === 'running' ? 'inline-block' : 'none';
    }

    if (status === 'running') {
      if (DOM.batchProgressTitle) DOM.batchProgressTitle.textContent = isCoolingDown ? 'AI Queue Rate-Limit Cooldown ⏳' : 'Synthesizing AI Intelligence (Background)…';
      if (DOM.batchProgressDetail) {
        if (isCoolingDown && cooldownRemaining > 0) {
          DOM.batchProgressDetail.textContent = `Rate-limit cooldown active (${Math.round(cooldownRemaining)}s remaining). Resuming automatically…`;
        } else {
          DOM.batchProgressDetail.textContent = `Analyzing Burst ${analyzed} of ${total} (${pct}%)…`;
        }
      }
      if (DOM.btnBatchRetry) DOM.btnBatchRetry.style.display = 'none';
    } else if (status === 'completed') {
      if (DOM.batchProgressTitle) DOM.batchProgressTitle.textContent = 'Batch AI Analysis Complete ✔';
      if (DOM.batchProgressDetail) DOM.batchProgressDetail.textContent = `Successfully synthesized ${total} bursts!`;
      if (DOM.btnBatchRetry) DOM.btnBatchRetry.style.display = 'none';
      setTimeout(() => {
        hideBatchProgress();
      }, 5000);
    } else if (status === 'failed') {
      if (DOM.batchProgressTitle) DOM.batchProgressTitle.textContent = 'Batch Analysis Halted / Error ⚠️';
      if (DOM.batchProgressDetail) DOM.batchProgressDetail.textContent = `Error at burst ${analyzed} of ${total}: ${errorMsg || 'Failed'}`;
      if (DOM.btnBatchRetry) DOM.btnBatchRetry.style.display = 'inline-block';
    }
  }

  function hideBatchProgress() {
    if (DOM.batchProgressContainer) DOM.batchProgressContainer.style.display = 'none';
  }

  function startBatchProgressPolling(projectName) {
    if (batchPollInterval) clearInterval(batchPollInterval);

    showBatchProgress(0, 1, 'running', null, false, 0);
    updateGlobalTray(projectName, 0, 1, 'running', null, false, 0);

    batchPollInterval = setInterval(async () => {
      try {
        const res = await fetch(`/api/project/${encodeURIComponent(projectName)}/analyze-batch/status`);
        const data = await res.json();
        const cooling = !!data.cooling_down;
        const cooldownS = Number(data.cooldown_remaining_s || 0);

        if (data.status === 'running') {
          showBatchProgress(data.analyzed_count || 0, data.total_events || 1, 'running', null, cooling, cooldownS);
          updateGlobalTray(projectName, data.analyzed_count || 0, data.total_events || 1, 'running', null, cooling, cooldownS);
        } else if (data.status === 'completed') {
          clearInterval(batchPollInterval);
          batchPollInterval = null;
          showBatchProgress(data.total_events || 0, data.total_events || 0, 'completed', null, false, 0);
          updateGlobalTray(projectName, data.total_events || 0, data.total_events || 0, 'completed', null, false, 0);
          if (DOM.btnBatchAnalyzeSession) {
            DOM.btnBatchAnalyzeSession.disabled = false;
            DOM.btnBatchAnalyzeSession.innerHTML = '<span class="ai-sparkle">✨</span> Batch Analyze Session';
          }
          showToast(`Batch AI synthesis completed successfully for ${projectName}!`, 'success', 'Batch Analysis');
          await loadProjectEvents(projectName);
        } else if (data.status === 'failed') {
          clearInterval(batchPollInterval);
          batchPollInterval = null;
          showBatchProgress(data.analyzed_count || 0, data.total_events || 0, 'failed', data.error, false, 0);
          updateGlobalTray(projectName, data.analyzed_count || 0, data.total_events || 0, 'failed', data.error, false, 0);
          if (DOM.btnBatchAnalyzeSession) {
            DOM.btnBatchAnalyzeSession.disabled = false;
            DOM.btnBatchAnalyzeSession.innerHTML = '<span class="ai-sparkle">✨</span> Batch Analyze Session';
          }
          showToast(`Batch AI synthesis halted: ${data.error || 'Failed'}`, 'error', 'Batch Analysis Failed');
        }
      } catch (err) {
        console.warn('Batch status polling error:', err);
      }
    }, 1200);
  }

  async function handleBatchAnalyzeSession(forceAll = false) {
    if (!state.selectedProject) {
      showToast('Please select a project first.', 'error');
      return;
    }

    if (DOM.btnBatchAnalyzeSession) {
      DOM.btnBatchAnalyzeSession.disabled = true;
      DOM.btnBatchAnalyzeSession.innerHTML = '<span class="ai-sparkle">✨</span> Analyzing…';
    }

    try {
      const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/analyze-batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force_all: forceAll }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Batch analysis request failed');
      }

      if (data.total_events === 0) {
        showToast('All prompt bursts already analyzed!', 'success');
        hideBatchProgress();
        if (DOM.btnBatchAnalyzeSession) {
          DOM.btnBatchAnalyzeSession.disabled = false;
          DOM.btnBatchAnalyzeSession.innerHTML = '<span class="ai-sparkle">✨</span> Batch Analyze Session';
        }
        return;
      }

      showToast(`Batch analysis started for ${data.total_events} bursts in background.`, 'info');
      startBatchProgressPolling(state.selectedProject);
    } catch (err) {
      console.error('Batch analyze error:', err);
      showToast(`Batch analyze failed: ${err.message}`, 'error');
      if (DOM.btnBatchAnalyzeSession) {
        DOM.btnBatchAnalyzeSession.disabled = false;
        DOM.btnBatchAnalyzeSession.innerHTML = '<span class="ai-sparkle">✨</span> Batch Analyze Session';
      }
    }
  }

  function handleBatchRetry() {
    handleBatchAnalyzeSession(false);
  }

  // ---------------------------------------------------------------------------
  // Project Deletion (5 Scopes) & Trash Bin Management
  // ---------------------------------------------------------------------------

  async function updateTrashCount() {
    try {
      const res = await fetch('/api/trash');
      const data = await res.json();
      const count = (data && typeof data.count === 'number') ? data.count : 0;
      if (DOM.headerTrashCount) DOM.headerTrashCount.textContent = count;
      if (DOM.badgeTrashCount) DOM.badgeTrashCount.textContent = count;
      const b1 = document.getElementById('badge-trash-count');
      if (b1) b1.textContent = count;
      const b2 = document.getElementById('header-trash-count');
      if (b2) b2.textContent = count;
      document.querySelectorAll('.badge-trash-count').forEach((el) => {
        el.textContent = count;
      });
    } catch (_) {}
  }

  function openDeleteProjectModal() {
    if (!state.selectedProject) {
      showToast('Please select a project to delete.', 'error');
      return;
    }

    if (DOM.delModalProjectName) {
      DOM.delModalProjectName.textContent = state.selectedProject;
    }
    if (DOM.deleteProjectError) {
      DOM.deleteProjectError.classList.add('hidden');
      DOM.deleteProjectError.textContent = '';
    }
    if (DOM.chkDeleteRemoteGithub) {
      DOM.chkDeleteRemoteGithub.checked = false;
    }

    // Default to local_session
    const radios = document.querySelectorAll('input[name="delete-scope"]');
    radios.forEach((r) => {
      r.checked = r.value === 'local_session';
      const opt = r.closest('.scope-option');
      if (opt) opt.classList.toggle('selected', r.checked);
    });

    if (DOM.deleteBurstsContainer) DOM.deleteBurstsContainer.style.display = 'none';
    if (DOM.deleteRemoteOption) DOM.deleteRemoteOption.style.display = 'none';

    // Populate selective bursts list
    if (DOM.deleteBurstsList) {
      DOM.deleteBurstsList.innerHTML = '';
      const editEvents = state.eventsData.filter((ev) => !ev.event_type || ev.event_type === 'EDIT');
      if (editEvents.length === 0) {
        DOM.deleteBurstsList.innerHTML = '<div class="empty-hint">No edit bursts recorded in session.db</div>';
      } else {
        editEvents.forEach((ev) => {
          const item = document.createElement('label');
          item.className = 'burst-check-item';
          const file = ev.first_file_touched || 'edit';
          const summary = ev.summary ? ` — ${ev.summary}` : '';
          const burstLabel = ev.session_burst_label || `Burst #${ev.burst_num || ev.id}`;
          item.innerHTML = `
            <input type="checkbox" value="${ev.id}" class="burst-del-chk">
            <span><strong>${escapeHtml(burstLabel)}</strong> • <span class="mono">${escapeHtml(file)}</span>${escapeHtml(summary)}</span>
          `;
          DOM.deleteBurstsList.appendChild(item);
        });
      }
    }

    if (DOM.modalDeleteProject) DOM.modalDeleteProject.classList.add('open');
  }

  function closeDeleteProjectModal() {
    if (DOM.modalDeleteProject) DOM.modalDeleteProject.classList.remove('open');
  }

  function setupDeleteScopeOptions() {
    const radios = document.querySelectorAll('input[name="delete-scope"]');
    radios.forEach((r) => {
      r.addEventListener('change', () => {
        radios.forEach((radio) => {
          const opt = radio.closest('.scope-option');
          if (opt) opt.classList.toggle('selected', radio.checked);
        });

        const val = r.value;
        if (DOM.deleteBurstsContainer) {
          DOM.deleteBurstsContainer.style.display = val === 'selective_bursts' ? 'block' : 'none';
        }
        if (DOM.deleteRemoteOption) {
          DOM.deleteRemoteOption.style.display = val === 'complete_erase' ? 'block' : 'none';
        }
      });
    });

    if (DOM.btnSelectAllBursts) {
      DOM.btnSelectAllBursts.addEventListener('click', () => {
        const chks = document.querySelectorAll('.burst-del-chk');
        const allChecked = Array.from(chks).every((c) => c.checked);
        chks.forEach((c) => { c.checked = !allChecked; });
        DOM.btnSelectAllBursts.textContent = allChecked ? 'Select All' : 'Deselect All';
      });
    }
  }

  async function handleConfirmDeleteProject() {
    if (!state.selectedProject) return;

    const checkedRadio = document.querySelector('input[name="delete-scope"]:checked');
    const scope = checkedRadio ? checkedRadio.value : 'local_session';
    const deleteRemote = DOM.chkDeleteRemoteGithub ? DOM.chkDeleteRemoteGithub.checked : false;

    let burstIds = [];
    if (scope === 'selective_bursts') {
      const chks = document.querySelectorAll('.burst-del-chk:checked');
      burstIds = Array.from(chks).map((c) => parseInt(c.value, 10));
      if (burstIds.length === 0) {
        if (DOM.deleteProjectError) {
          DOM.deleteProjectError.textContent = 'Please select at least one burst to delete.';
          DOM.deleteProjectError.classList.remove('hidden');
        }
        return;
      }
    }

    if (scope === 'complete_erase' && deleteRemote) {
      if (!confirm(`CAUTION: You have chosen to PERMANENTLY DELETE the remote GitHub repository for '${state.selectedProject}'. This cannot be undone from the Trash Bin. Continue?`)) {
        return;
      }
    }

    if (DOM.deleteProjectSpinner) DOM.deleteProjectSpinner.classList.remove('hidden');
    if (DOM.btnConfirmDeleteProject) DOM.btnConfirmDeleteProject.disabled = true;

    try {
      const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: scope,
          burst_ids: burstIds,
          delete_remote: deleteRemote,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Deletion failed');
      }

      showToast(`Project data moved to Trash successfully (${scope}).`, 'success');
      closeDeleteProjectModal();
      await updateTrashCount();

      if (scope === 'selective_bursts') {
        await loadProjectEvents(state.selectedProject);
      } else {
        await refreshProjects();
      }
    } catch (err) {
      console.error('Delete project error:', err);
      if (DOM.deleteProjectError) {
        DOM.deleteProjectError.textContent = `Deletion error: ${err.message}`;
        DOM.deleteProjectError.classList.remove('hidden');
      }
      showToast(`Delete failed: ${err.message}`, 'error');
    } finally {
      if (DOM.deleteProjectSpinner) DOM.deleteProjectSpinner.classList.add('hidden');
      if (DOM.btnConfirmDeleteProject) DOM.btnConfirmDeleteProject.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Trash Bin Modal Operations
  // ---------------------------------------------------------------------------

  function openTrashBinModal() {
    if (DOM.modalTrashBin) DOM.modalTrashBin.classList.add('open');
    loadTrashBinItems();
  }

  function closeTrashBinModal() {
    if (DOM.modalTrashBin) DOM.modalTrashBin.classList.remove('open');
  }

  async function loadTrashBinItems() {
    if (!DOM.trashBinItemsContainer) return;
    DOM.trashBinItemsContainer.innerHTML = '<div class="empty-state">Loading trash bin…</div>';

    try {
      const res = await fetch('/api/trash');
      const data = await res.json();
      if (!data.success) throw new Error(data.message || data.error || 'Failed to list trash');

      const items = data.items || [];
      if (DOM.headerTrashCount) DOM.headerTrashCount.textContent = items.length;

      if (items.length === 0) {
        DOM.trashBinItemsContainer.innerHTML = '<div class="empty-state">Trash Bin is empty. No soft-deleted items.</div>';
        return;
      }

      const fragment = document.createDocumentFragment();
      items.forEach((item) => {
        const card = document.createElement('div');
        card.className = 'trash-item-card';

        const scopeLabel = (item.scope || 'Item').replace(/_/g, ' ');
        const timeStr = formatISTTime(item.timestamp);
        const sizeKb = item.folder_size_bytes ? `${Math.round(item.folder_size_bytes / 1024)} KB` : '';
        const details = item.details || {};
        let detailText = '';
        if (details.burst_ids) {
          detailText = `Bursts: #${details.burst_ids.join(', #')}`;
        } else if (details.moved_items) {
          detailText = `Moved: ${details.moved_items.join(', ')}`;
        }

        card.innerHTML = `
          <div class="trash-item-main">
            <div class="trash-item-top">
              <span class="trash-item-title">${escapeHtml(item.project_name || 'Unknown')}</span>
              <span class="trash-scope-badge">${escapeHtml(scopeLabel)}</span>
            </div>
            <div class="trash-item-meta">
              <span>Deleted: ${escapeHtml(timeStr)}</span>
              ${detailText ? `<span>${escapeHtml(detailText)}</span>` : ''}
              ${sizeKb ? `<span>Size: ${sizeKb}</span>` : ''}
            </div>
          </div>
          <div class="trash-item-actions">
            <button type="button" class="btn btn-secondary btn-xs btn-restore-trash" data-trash-id="${escapeHtml(item.trash_id)}">
              ⟲ Restore
            </button>
            <button type="button" class="btn btn-danger-outline btn-xs btn-purge-trash" data-trash-id="${escapeHtml(item.trash_id)}">
              ✕ Purge
            </button>
          </div>
        `;

        card.querySelector('.btn-restore-trash').addEventListener('click', (e) => handleRestoreTrash(item.trash_id, e.currentTarget));
        card.querySelector('.btn-purge-trash').addEventListener('click', (e) => handlePurgeTrashItem(item.trash_id, e.currentTarget));

        fragment.appendChild(card);
      });

      DOM.trashBinItemsContainer.innerHTML = '';
      DOM.trashBinItemsContainer.appendChild(fragment);
    } catch (err) {
      DOM.trashBinItemsContainer.innerHTML = `<div class="empty-state" style="color: #ef4444;">Failed to load trash bin: ${escapeHtml(err.message)}</div>`;
    }
  }

  async function handleRestoreTrash(trashId, btnEl) {
    const originalText = btnEl ? btnEl.innerHTML : null;
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = 'Restoring…';
    }
    try {
      showToast(`Restoring '${trashId}'…`, 'info');
      const res = await fetch(`/api/trash/restore/${encodeURIComponent(trashId)}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Restore failed');
      }
      showToast(`Restored successfully!`, 'success');
      await loadTrashBinItems();
      await updateTrashCount();
      await refreshProjects();
    } catch (err) {
      console.error('Restore error:', err);
      showToast(`Restore failed: ${err.message}`, 'error');
      if (btnEl && originalText) {
        btnEl.disabled = false;
        btnEl.innerHTML = originalText;
      }
    }
  }

  async function handlePurgeTrashItem(trashId, btnEl) {
    if (!confirm(`Are you sure you want to PERMANENTLY PURGE '${trashId}' from disk? This cannot be restored.`)) {
      return;
    }
    const originalText = btnEl ? btnEl.innerHTML : null;
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = '<span class="spinner-sm"></span> Purging…';
    }
    try {
      const res = await fetch(`/api/trash/purge/${encodeURIComponent(trashId)}`, { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Purge failed');
      }
      showToast(`Permanently purged '${trashId}'.`, 'success');
      await loadTrashBinItems();
      await updateTrashCount();
    } catch (err) {
      console.error('Purge error:', err);
      showToast(`Purge failed: ${err.message}`, 'error');
      if (btnEl && originalText) {
        btnEl.disabled = false;
        btnEl.innerHTML = originalText;
      }
    }
  }

  const handlePurgeTrash = handlePurgeTrashItem;

  async function handleEmptyTrashBin() {
    if (!confirm('Are you sure you want to PERMANENTLY EMPTY the entire Trash Bin? All soft-deleted items will be wiped from disk.')) {
      return;
    }
    const btn = DOM.btnEmptyTrash;
    const originalText = btn ? btn.innerHTML : null;
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-sm"></span> Purging…';
    }
    try {
      const res = await fetch('/api/trash/purge-all', { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || data.error || 'Empty trash failed');
      }
      showToast(`Trash Bin emptied (${data.purged_count || 0} items purged).`, 'success');
      await loadTrashBinItems();
      await updateTrashCount();
    } catch (err) {
      console.error('Empty trash error:', err);
      showToast(`Empty trash failed: ${err.message}`, 'error');
    } finally {
      if (btn && originalText) {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    }
  }

  // ---------------------------------------------------------------------------
  // AI Settings Modal & Providers
  // ---------------------------------------------------------------------------

  function updateAiProviderBoxes(provider) {
    if (!DOM.boxProviderOllama || !DOM.boxProviderGemini || !DOM.boxProviderHeuristic) return;
    DOM.boxProviderOllama.style.display = provider === 'ollama' ? 'flex' : 'none';
    DOM.boxProviderGemini.style.display = provider === 'gemini' ? 'flex' : 'none';
    DOM.boxProviderHeuristic.style.display = provider === 'heuristic' ? 'flex' : 'none';
  }

  async function openAiSettingsModal() {
    if (!DOM.modalAiSettings) return;
    if (DOM.ollamaTestStatus) {
      DOM.ollamaTestStatus.className = 'test-status-badge hidden';
      DOM.ollamaTestStatus.textContent = '';
    }
    if (DOM.geminiTestStatus) {
      DOM.geminiTestStatus.className = 'test-status-badge hidden';
      DOM.geminiTestStatus.textContent = '';
    }
    DOM.modalAiSettings.classList.add('open');

    try {
      const res = await fetch('/api/ai/config');
      const data = await res.json();
      if (data.success && data.config) {
        const c = data.config;
        DOM.selAiProvider.value = c.preferred_provider || 'heuristic';
        DOM.inpOllamaUrl.value = c.ollama_base_url || 'http://127.0.0.1:11434';
        DOM.inpOllamaModel.value = c.ollama_model || 'qwen2.5-coder:7b';
        DOM.selGeminiModel.value = c.gemini_model || 'gemini-3.1-flash-lite';

        state.savedGeminiKeys = c.saved_gemini_keys || c.saved_keys || [];
        populateGeminiKeyVaultDropdown(state.savedGeminiKeys, c);

        updateAiProviderBoxes(DOM.selAiProvider.value);
      }
    } catch (err) {
      console.warn('Failed to load AI config:', err);
    }
  }

  function populateGeminiKeyVaultDropdown(keys, config) {
    if (!DOM.selGeminiKeyVault) return;
    DOM.selGeminiKeyVault.innerHTML = '';

    let activeKeyId = null;
    keys.forEach((k) => {
      const opt = document.createElement('option');
      opt.value = k.id;
      opt.textContent = `${k.label || k.suffix} ${k.active ? '★ Active' : ''}`;
      if (k.active) {
        opt.selected = true;
        activeKeyId = k.id;
      }
      DOM.selGeminiKeyVault.appendChild(opt);
    });

    const newOpt = document.createElement('option');
    newOpt.value = '__new__';
    newOpt.textContent = '+ Add / Paste New Key';
    DOM.selGeminiKeyVault.appendChild(newOpt);

    if (!activeKeyId && keys.length === 0) {
      newOpt.selected = true;
    }

    onGeminiKeyVaultSelectionChanged();
  }

  function onGeminiKeyVaultSelectionChanged() {
    if (!DOM.selGeminiKeyVault) return;
    const selectedVal = DOM.selGeminiKeyVault.value;

    if (selectedVal === '__new__') {
      DOM.inpGeminiKey.value = '';
      DOM.inpGeminiKey.disabled = false;
      DOM.inpGeminiKey.placeholder = 'AIzaSy•••••••••••••••••••••••••••••••';
      if (DOM.btnDeleteGeminiKey) DOM.btnDeleteGeminiKey.style.display = 'none';
      if (DOM.geminiActiveKeyInfo) {
        DOM.geminiActiveKeyInfo.textContent = 'New Key Entry';
        DOM.geminiActiveKeyInfo.style.color = '#a855f7';
      }
      if (DOM.helpGeminiKey) {
        DOM.helpGeminiKey.innerHTML = 'Pasting a new key saves it permanently to the vault and activates it upon save.';
      }
    } else {
      const selectedKey = (state.savedGeminiKeys || []).find((k) => k.id === selectedVal);
      if (selectedKey) {
        DOM.inpGeminiKey.value = selectedKey.masked_key || '••••••••••••••••';
        DOM.inpGeminiKey.disabled = true;
        if (DOM.btnDeleteGeminiKey) DOM.btnDeleteGeminiKey.style.display = 'inline-flex';
        if (DOM.geminiActiveKeyInfo) {
          DOM.geminiActiveKeyInfo.textContent = selectedKey.active ? '★ Active Key' : 'Saved in Vault';
          DOM.geminiActiveKeyInfo.style.color = selectedKey.active ? '#4ade80' : '#94a3b8';
        }
        if (DOM.helpGeminiKey) {
          DOM.helpGeminiKey.innerHTML = `Added: ${escapeHtml(formatIST(selectedKey.added_at))} &bull; Last used: ${escapeHtml(formatIST(selectedKey.last_used))}`;
        }
      }
    }
  }

  async function handleDeleteGeminiKey() {
    if (!DOM.selGeminiKeyVault) return;
    const selectedVal = DOM.selGeminiKeyVault.value;
    if (selectedVal === '__new__') return;

    if (!confirm('Are you sure you want to remove this API key from the vault?')) {
      return;
    }

    DOM.btnDeleteGeminiKey.disabled = true;
    try {
      const res = await fetch('/api/ai/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delete_key_id: selectedVal }),
      });
      const data = await res.json();
      if (data.success && data.config) {
        showToast('Key removed from vault.', 'success');
        state.savedGeminiKeys = data.config.saved_gemini_keys || data.config.saved_keys || [];
        populateGeminiKeyVaultDropdown(state.savedGeminiKeys, data.config);
      } else {
        showToast(`Failed to remove key: ${data.message || data.error}`, 'error');
      }
    } catch (err) {
      showToast(`Delete failed: ${err.message}`, 'error');
    } finally {
      DOM.btnDeleteGeminiKey.disabled = false;
    }
  }

  function closeAiSettingsModal() {
    if (DOM.modalAiSettings) DOM.modalAiSettings.classList.remove('open');
  }

  async function handleTestAiConnection(provider) {
    const badge = provider === 'ollama' ? DOM.ollamaTestStatus : DOM.geminiTestStatus;
    const btn = provider === 'ollama' ? DOM.btnTestOllama : DOM.btnTestGemini;
    if (!badge || !btn) return;

    btn.disabled = true;
    badge.className = 'test-status-badge testing';
    badge.textContent = 'Connecting…';

    const payload = { provider: provider };
    if (provider === 'ollama') {
      payload.base_url = DOM.inpOllamaUrl.value.trim();
      payload.model = DOM.inpOllamaModel.value.trim();
    } else if (provider === 'gemini') {
      const vaultVal = DOM.selGeminiKeyVault ? DOM.selGeminiKeyVault.value : '__new__';
      if (vaultVal !== '__new__') {
        payload.key_id = vaultVal;
      } else {
        payload.api_key = DOM.inpGeminiKey.value.trim();
      }
      payload.model = DOM.selGeminiModel.value;
    }

    try {
      const res = await fetch('/api/ai/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        badge.className = 'test-status-badge success';
        badge.textContent = `✔ ${data.message}`;
      } else {
        badge.className = 'test-status-badge error';
        badge.textContent = `✖ ${data.message || data.error}`;
      }
    } catch (err) {
      badge.className = 'test-status-badge error';
      badge.textContent = `✖ Error: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  }

  async function handleSaveAiSettings(e) {
    if (e) e.preventDefault();
    const vaultVal = DOM.selGeminiKeyVault ? DOM.selGeminiKeyVault.value : '__new__';
    const payload = {
      preferred_provider: DOM.selAiProvider.value,
      ollama_base_url: DOM.inpOllamaUrl.value.trim(),
      ollama_model: DOM.inpOllamaModel.value.trim(),
      gemini_model: DOM.selGeminiModel.value,
    };

    if (vaultVal !== '__new__') {
      payload.select_key_id = vaultVal;
    } else {
      payload.gemini_api_key = DOM.inpGeminiKey.value.trim();
    }

    DOM.btnSaveAiConfig.disabled = true;
    try {
      const res = await fetch('/api/ai/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        showToast('AI settings saved successfully!', 'success');
        closeAiSettingsModal();
      } else {
        showToast(`Save failed: ${data.message || data.error}`, 'error');
      }
    } catch (err) {
      showToast(`Save failed: ${err.message}`, 'error');
    } finally {
      DOM.btnSaveAiConfig.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Tab Switching
  // ---------------------------------------------------------------------------

  function selectTab(tabName) {
    state.activeTab = tabName;
    DOM.navButtons.forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    DOM.tabViews.forEach((view) => {
      view.classList.toggle('active', view.id === `view-${tabName}`);
    });

    if ((tabName === 'timeline' || tabName === 'diffs' || tabName === 'terminal') && state.selectedProject) {
      loadProjectEvents(state.selectedProject);
    } else if (tabName === 'logs' && state.selectedProject) {
      loadProjectLogs(state.selectedProject);
    }
  }

  // ---------------------------------------------------------------------------
  // Event Listeners Setup
  // ---------------------------------------------------------------------------

  function setupEventListeners() {
    // Nav tabs
    DOM.navButtons.forEach((btn) => {
      btn.addEventListener('click', () => selectTab(btn.dataset.tab));
    });

    // Dashboard quick link
    DOM.btnViewAllTimeline.addEventListener('click', () => selectTab('timeline'));

    // Project search
    DOM.projectFilterInput.addEventListener('input', renderProjectsList);
    DOM.btnRefreshProjects.addEventListener('click', refreshProjects);

    // Header buttons
    DOM.btnHeaderStop.addEventListener('click', handleStopActiveProject);
    if (DOM.btnHeaderResume) {
      DOM.btnHeaderResume.addEventListener('click', handleResumeProject);
    }
    if (DOM.btnTelemetryResume) {
      DOM.btnTelemetryResume.addEventListener('click', handleResumeProject);
    }
    if (DOM.btnHeaderGitSettings) {
      DOM.btnHeaderGitSettings.addEventListener('click', openGitSettingsModal);
    }
    if (DOM.btnHeaderPushGit) {
      DOM.btnHeaderPushGit.addEventListener('click', handleOpenSyncReview);
    }
    if (DOM.btnSpecsPushGh) {
      DOM.btnSpecsPushGh.addEventListener('click', handleOpenSyncReview);
    }
    DOM.btnExportSession.addEventListener('click', handleExportSession);

    // Timeline filters
    setupTimelineFilters();

    // Timeline & Diffs & Terminal
    DOM.btnRefreshTimeline.addEventListener('click', () => loadProjectEvents(state.selectedProject));
    if (DOM.btnRefreshTerminal) {
      DOM.btnRefreshTerminal.addEventListener('click', () => loadProjectEvents(state.selectedProject));
    }
    if (DOM.diffSessionSelect) {
      DOM.diffSessionSelect.addEventListener('change', (e) => onDiffSessionSelected(e.target.value));
    }
    if (DOM.diffBurstSelect) {
      DOM.diffBurstSelect.addEventListener('change', (e) => onDiffBurstSelected(e.target.value));
    }

    // Logs controls
    DOM.chkAutoscroll.addEventListener('change', (e) => {
      state.autoScrollLogs = e.target.checked;
    });
    DOM.btnRefreshLogs.addEventListener('click', () => loadProjectLogs(state.selectedProject));
    DOM.btnCopyLogs.addEventListener('click', () => {
      navigator.clipboard.writeText(DOM.logsTerminal.innerText);
      showToast('Logs copied to clipboard', 'success');
    });

    // Sidebar Accordions
    document.querySelectorAll('.accordion-header').forEach((hdr) => {
      hdr.addEventListener('click', () => {
        const section = hdr.closest('.accordion-section');
        if (section) section.classList.toggle('open');
      });
    });

    // Batch analyze session button
    if (DOM.btnBatchAnalyzeSession) {
      DOM.btnBatchAnalyzeSession.addEventListener('click', handleBatchAnalyzeSession);
    }

    // Modal controls: New Session
    DOM.btnNewSession.addEventListener('click', openNewSessionModal);
    DOM.modalCloseBtn.addEventListener('click', closeNewSessionModal);
    DOM.modalCancelBtn.addEventListener('click', closeNewSessionModal);
    DOM.formNewSession.addEventListener('submit', handleLaunchNewSession);
    if (DOM.selIdeProfile) {
      DOM.selIdeProfile.addEventListener('change', (e) => {
        if (DOM.groupCustomIdeMarker) {
          DOM.groupCustomIdeMarker.classList.toggle('hidden', e.target.value !== 'custom');
        }
      });
    }

    // Modal controls: Git Settings
    if (DOM.modalGitSettingsClose) DOM.modalGitSettingsClose.addEventListener('click', closeGitSettingsModal);
    if (DOM.modalGitSettingsCancel) DOM.modalGitSettingsCancel.addEventListener('click', closeGitSettingsModal);
    if (DOM.formGitSettings) DOM.formGitSettings.addEventListener('submit', handleSaveGitSettings);
    if (DOM.btnTestGitConnection) DOM.btnTestGitConnection.addEventListener('click', handleTestGitConnection);

    // Modal controls: Milestone Sync Review
    if (DOM.modalSyncReviewClose) DOM.modalSyncReviewClose.addEventListener('click', closeSyncReviewModal);
    if (DOM.modalSyncCancel) DOM.modalSyncCancel.addEventListener('click', closeSyncReviewModal);
    if (DOM.btnConfirmSyncPush) DOM.btnConfirmSyncPush.addEventListener('click', handleConfirmSyncPush);

    // Modal controls: AI Model Settings
    if (DOM.btnHeaderAiSettings) DOM.btnHeaderAiSettings.addEventListener('click', openAiSettingsModal);
    if (DOM.modalAiSettingsClose) DOM.modalAiSettingsClose.addEventListener('click', closeAiSettingsModal);
    if (DOM.modalAiSettingsCancel) DOM.modalAiSettingsCancel.addEventListener('click', closeAiSettingsModal);
    if (DOM.formAiSettings) DOM.formAiSettings.addEventListener('submit', handleSaveAiSettings);
    if (DOM.selAiProvider) {
      DOM.selAiProvider.addEventListener('change', (e) => updateAiProviderBoxes(e.target.value));
    }
    if (DOM.btnTestOllama) {
      DOM.btnTestOllama.addEventListener('click', () => handleTestAiConnection('ollama'));
    }
    if (DOM.btnTestGemini) {
      DOM.btnTestGemini.addEventListener('click', () => handleTestAiConnection('gemini'));
    }
    if (DOM.selGeminiKeyVault) {
      DOM.selGeminiKeyVault.addEventListener('change', onGeminiKeyVaultSelectionChanged);
    }
    if (DOM.btnDeleteGeminiKey) {
      DOM.btnDeleteGeminiKey.addEventListener('click', handleDeleteGeminiKey);
    }

    // Live slug preview
    DOM.inpProjectName.addEventListener('input', (e) => {
      DOM.slugPreview.textContent = slugify(e.target.value);
    });

    // Debounce slider display & presets
    DOM.rngDebounce.addEventListener('input', (e) => {
      const val = parseFloat(e.target.value);
      const valFixed = val.toFixed(1);
      let badge = '';
      if (valFixed === '3.5') {
        badge = '<span class="badge badge-recommended">Fast (Recommended)</span>';
      } else if (val >= 9.5 && val <= 10.5) {
        badge = '<span class="badge badge-subtle">Medium</span>';
      } else if (val >= 58 && val <= 62) {
        badge = '<span class="badge badge-subtle">1m</span>';
      } else if (val >= 295 && val <= 305) {
        badge = '<span class="badge badge-subtle">5m</span>';
      } else if (val >= 595 && val <= 605) {
        badge = '<span class="badge badge-subtle">10m</span>';
      } else if (val >= 1795 && val <= 1805) {
        badge = '<span class="badge badge-subtle">30m</span>';
      }

      let timeLabel = `${valFixed}s`;
      if (val >= 60) {
        const mins = Math.floor(val / 60);
        const secs = Math.round(val % 60);
        timeLabel = `${mins}m ${secs > 0 ? secs + 's ' : ''}(${valFixed}s)`;
      }
      DOM.debounceValDisplay.innerHTML = `${timeLabel} ${badge}`;
    });

    // Preset buttons for debounce window
    document.querySelectorAll('.btn-debounce-preset').forEach((btn) => {
      btn.addEventListener('click', () => {
        const val = parseFloat(btn.dataset.val);
        if (!isNaN(val) && DOM.rngDebounce) {
          DOM.rngDebounce.value = val;
          DOM.rngDebounce.dispatchEvent(new Event('input'));
        }
      });
    });

    // Lap Button: Manual Burst Sealing
    if (DOM.btnSealBurstNow) {
      DOM.btnSealBurstNow.addEventListener('click', async () => {
        if (!state.selectedProject) return;
        DOM.btnSealBurstNow.disabled = true;
        DOM.btnSealBurstNow.innerHTML = '<span class="spinner-sm"></span> Sealing…';
        try {
          const res = await fetch(`/api/project/${encodeURIComponent(state.selectedProject)}/seal-burst`, {
            method: 'POST',
          });
          const data = await res.json();
          if (!res.ok || !data.success) {
            throw new Error(data.message || data.error || 'Seal burst failed');
          }
          showToast('Burst sealed immediately (Lap triggered)!', 'success');
          DOM.debounceProgressFill.style.width = '0%';
          DOM.debounceProgressFill.classList.remove('active');
          DOM.debouncePulse.className = 'debounce-pulse-indicator idle';
          DOM.debounceStatusBadge.className = 'debounce-status-badge idle';
          DOM.debounceStatusBadge.textContent = 'Idle / Listening';
          DOM.debounceStatusDesc.textContent = 'Watching for file modifications…';
          DOM.debounceCountdownText.textContent = '—';
          DOM.btnSealBurstNow.style.display = 'none';
          await loadProjectEvents(state.selectedProject);
        } catch (err) {
          console.error('Seal burst error:', err);
          showToast(`Seal burst failed: ${err.message}`, 'error');
        } finally {
          DOM.btnSealBurstNow.disabled = false;
          DOM.btnSealBurstNow.innerHTML = '⚡ Seal Burst Now (Lap)';
        }
      });
    }

    // Trash Bin Header & Modal
    if (DOM.btnHeaderTrash) {
      DOM.btnHeaderTrash.addEventListener('click', openTrashBinModal);
    }
    if (DOM.modalTrashBinClose) {
      DOM.modalTrashBinClose.addEventListener('click', closeTrashBinModal);
    }
    if (DOM.btnEmptyTrash) {
      DOM.btnEmptyTrash.addEventListener('click', handleEmptyTrashBin);
    }

    // Delete Project Header & Modal
    if (DOM.btnHeaderDeleteProj) {
      DOM.btnHeaderDeleteProj.addEventListener('click', openDeleteProjectModal);
    }
    if (DOM.modalDeleteProjectClose) {
      DOM.modalDeleteProjectClose.addEventListener('click', closeDeleteProjectModal);
    }
    if (DOM.modalDeleteProjectCancel) {
      DOM.modalDeleteProjectCancel.addEventListener('click', closeDeleteProjectModal);
    }
    if (DOM.btnConfirmDeleteProject) {
      DOM.btnConfirmDeleteProject.addEventListener('click', handleConfirmDeleteProject);
    }
    setupDeleteScopeOptions();

    // Batch retry
    if (DOM.btnBatchRetry) {
      DOM.btnBatchRetry.addEventListener('click', handleBatchRetry);
    }

    // Close modals on backdrop click
    DOM.modalNewSession.addEventListener('click', (e) => {
      if (e.target === DOM.modalNewSession) closeNewSessionModal();
    });
    if (DOM.modalGitSettings) {
      DOM.modalGitSettings.addEventListener('click', (e) => {
        if (e.target === DOM.modalGitSettings) closeGitSettingsModal();
      });
    }
    if (DOM.modalSyncReview) {
      DOM.modalSyncReview.addEventListener('click', (e) => {
        if (e.target === DOM.modalSyncReview) closeSyncReviewModal();
      });
    }
    if (DOM.modalAiSettings) {
      DOM.modalAiSettings.addEventListener('click', (e) => {
        if (e.target === DOM.modalAiSettings) closeAiSettingsModal();
      });
    }
    if (DOM.modalDeleteProject) {
      DOM.modalDeleteProject.addEventListener('click', (e) => {
        if (e.target === DOM.modalDeleteProject) closeDeleteProjectModal();
      });
    }
    if (DOM.modalTrashBin) {
      DOM.modalTrashBin.addEventListener('click', (e) => {
        if (e.target === DOM.modalTrashBin) closeTrashBinModal();
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Collapsible Sidebar Management
  // ---------------------------------------------------------------------------

  function setupSidebarToggle() {
    if (!DOM.sidebar) return;

    function applySidebarState(isCollapsed) {
      DOM.sidebar.classList.toggle('collapsed', isCollapsed);
      if (DOM.sidebarToggleIcon) {
        DOM.sidebarToggleIcon.textContent = isCollapsed ? '▶' : '◀';
      }
      try {
        localStorage.setItem('spd_sidebar_collapsed', String(isCollapsed));
      } catch (e) {}
    }

    // Read stored preference or auto-collapse if narrow viewport
    try {
      const stored = localStorage.getItem('spd_sidebar_collapsed');
      if (stored !== null) {
        applySidebarState(stored === 'true');
      } else if (window.innerWidth <= 780) {
        applySidebarState(true);
      }
    } catch (e) {}

    if (DOM.btnToggleSidebar) {
      DOM.btnToggleSidebar.addEventListener('click', (e) => {
        e.stopPropagation();
        const currentlyCollapsed = DOM.sidebar.classList.contains('collapsed');
        applySidebarState(!currentlyCollapsed);
      });
    }

    // Keyboard shortcut: Ctrl + B or Cmd + B
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 'b' || e.key === 'B')) {
        // Prevent default browser bookmark shortcut
        e.preventDefault();
        const currentlyCollapsed = DOM.sidebar.classList.contains('collapsed');
        applySidebarState(!currentlyCollapsed);
      }
    });

    // Auto-collapse on small window resize
    window.addEventListener('resize', () => {
      if (window.innerWidth <= 780 && !DOM.sidebar.classList.contains('collapsed')) {
        applySidebarState(true);
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Initialization
  // ---------------------------------------------------------------------------

  async function init() {
    setupSidebarToggle();
    setupEventListeners();
    await updateTrashCount();
    await refreshProjects();
    initSSE();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
```

---

### [26/27] File: `web\index.html`
- **Lines:** 1272 | **Size:** 66.18 KB | **Type:** html

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SPD Analysis Engine — Control Portal</title>
  <link rel="stylesheet" href="style.css">
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2310b981'><path d='M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5'/></svg>">
</head>
<body>
  <div class="app-layout">
    <!-- ============================================================= -->
    <!-- LEFT SIDEBAR                                                  -->
    <!-- ============================================================= -->
    <aside class="sidebar">
      <div class="sidebar-brand">
        <div class="brand-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
          </svg>
        </div>
        <div class="brand-text">
          <h2>SPD Engine</h2>
          <span class="brand-sub">Passive AI Auditor</span>
        </div>
        <button id="btn-toggle-sidebar" class="btn-icon sidebar-toggle-btn" title="Toggle Sidebar (Ctrl+B)" aria-label="Toggle Sidebar">
          <span id="sidebar-toggle-icon">◀</span>
        </button>
      </div>

      <div class="sidebar-action">
        <button id="btn-new-session" class="btn btn-primary btn-block">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="8" x2="12" y2="16"/>
            <line x1="8" y1="12" x2="16" y2="12"/>
          </svg>
          <span class="nav-label">New Session</span>
        </button>
      </div>

      <!-- Navigation Tabs -->
      <nav class="sidebar-nav">
        <div class="nav-section-title">Navigation</div>
        <button class="nav-item active" data-tab="dashboard" title="Live Dashboard">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon">
            <rect x="3" y="3" width="7" height="9"/>
            <rect x="14" y="3" width="7" height="5"/>
            <rect x="14" y="12" width="7" height="9"/>
            <rect x="3" y="16" width="7" height="5"/>
          </svg>
          <span class="nav-label">Live Dashboard</span>
        </button>
        <button class="nav-item" data-tab="timeline" title="Event Timeline">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon">
            <circle cx="12" cy="12" r="10"/>
            <polyline points="12 6 12 12 16 14"/>
          </svg>
          <span class="nav-label">Event Timeline</span>
          <span class="badge badge-subtle" id="nav-event-count">0</span>
        </button>
        <button class="nav-item" data-tab="diffs" title="Patch Diff Viewer">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon">
            <line x1="18" y1="6" x2="6" y2="18"/>
            <line x1="6" y1="6" x2="18" y2="18"/>
            <circle cx="6" cy="6" r="3"/>
            <circle cx="18" cy="18" r="3"/>
          </svg>
          <span class="nav-label">Patch Diff Viewer</span>
        </button>
        <button class="nav-item" data-tab="terminal" title="Terminal & Commands">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon">
            <polyline points="4 17 10 11 4 5"/>
            <line x1="12" y1="19" x2="20" y2="19"/>
          </svg>
          <span class="nav-label">Terminal & Commands</span>
          <span class="badge badge-subtle" id="nav-exec-count">0</span>
        </button>
        <button class="nav-item" data-tab="logs" title="Worker Logs">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon">
            <polyline points="4 17 10 11 4 5"/>
            <line x1="12" y1="19" x2="20" y2="19"/>
          </svg>
          <span class="nav-label">Worker Logs</span>
        </button>
      </nav>

      <!-- Projects Explorer -->
      <div class="sidebar-projects">
        <div class="projects-header">
          <span class="nav-section-title">Project Tiers</span>
          <button id="btn-refresh-projects" class="btn-icon" title="Refresh projects list">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm">
              <path d="M23 4v6h-6M1 20v-6h6"/>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
            </svg>
          </button>
        </div>

        <div class="project-search">
          <input type="text" id="project-filter-input" placeholder="Filter projects…" />
        </div>

        <div class="projects-list-container">
          <div class="accordion-section open" id="accordion-current">
            <button type="button" class="accordion-header" data-accordion="current">
              <div class="accordion-title">
                <span class="accordion-chevron">▸</span>
                <span>Active Sessions</span>
              </div>
              <span class="tier-count" id="count-current">0</span>
            </button>
            <div class="accordion-body">
              <div class="tier-items" id="tier-current-items">
                <div class="empty-hint">No active sessions</div>
              </div>
            </div>
          </div>

          <div class="accordion-section open" id="accordion-last-run">
            <button type="button" class="accordion-header" data-accordion="last-run">
              <div class="accordion-title">
                <span class="accordion-chevron">▸</span>
                <span>Recent Runs (Last Run)</span>
              </div>
              <span class="tier-count" id="count-last-run">0</span>
            </button>
            <div class="accordion-body">
              <div class="tier-items" id="tier-last-run-items">
                <div class="empty-hint">No recent runs</div>
              </div>
            </div>
          </div>

          <div class="accordion-section" id="accordion-history">
            <button type="button" class="accordion-header" data-accordion="history">
              <div class="accordion-title">
                <span class="accordion-chevron">▸</span>
                <span>History Archive</span>
              </div>
              <span class="tier-count" id="count-history">0</span>
            </button>
            <div class="accordion-body">
              <div class="tier-items" id="tier-history-items">
                <div class="empty-hint">No archived history</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Server Connection Footer -->
      <div class="sidebar-footer">
        <div class="connection-status" id="connection-status">
          <span class="status-dot connected"></span>
          <span class="status-text" id="conn-text">SSE Stream Active</span>
        </div>
        <span class="app-version">v1.0.0</span>
      </div>
    </aside>

    <!-- ============================================================= -->
    <!-- MAIN CONTENT AREA                                             -->
    <!-- ============================================================= -->
    <main class="main-content">
      <!-- Top Header Bar -->
      <!-- Top Header Bar -->
      <header class="top-header header-bar">
        <!-- 1. Header Left: Project Title, Status Pill, and Monitored Path -->
        <div class="header-left">
          <div class="project-indicator">
            <span class="project-tier-pill" id="header-tier-pill">ACTIVE</span>
            <h1 class="header-project-name" id="header-project-name">Loading…</h1>
          </div>
          <div class="header-target-path" id="header-target-path" title="Monitored Workspace Path">
            <span class="path-label">Monitored:</span>
            <span class="path-val" id="header-path-val">—</span>
          </div>
        </div>

        <!-- 2. Header Center: Telemetry cards (PID, Uptime, Memory) -->
        <div class="header-center">
          <div class="telemetry-pill">
            <span class="tel-label">PID</span>
            <span class="tel-val" id="header-pid">—</span>
          </div>
          <div class="telemetry-pill">
            <span class="tel-label">UPTIME</span>
            <span class="tel-val" id="header-uptime">—</span>
          </div>
          <div class="telemetry-pill">
            <span class="tel-label">RAM</span>
            <span class="tel-val" id="header-mem">—</span>
          </div>
          <div class="telemetry-pill">
            <span class="tel-label">HEARTBEAT</span>
            <span class="tel-val" id="header-hb">—</span>
          </div>
          <!-- Compact condensed telemetry for split-screen / narrow widths -->
          <div class="telemetry-compact" id="tel-compact-pill" style="display: none;">
            <span id="header-compact-tel">🟢 Live • —</span>
          </div>
        </div>

        <!-- 3. Header Right: Toolbar containing action buttons -->
        <div class="header-right">
          <button id="btn-header-ai-settings" class="btn btn-secondary btn-sm" title="Configure AI model & intelligence settings">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm">
              <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm0 18a8 8 0 1 1 8-8 8 8 0 0 1-8 8z"/>
              <path d="M12 6v6l4 2"/>
            </svg>
            <span class="btn-text">AI Settings</span>
          </button>
          <button id="btn-header-git-settings" class="btn btn-secondary btn-sm" title="Configure GitHub credentials & default push settings">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
            <span class="btn-text">Git Settings</span>
          </button>
          <button id="btn-header-push-git" class="btn btn-secondary btn-sm" title="Push workspace changes to remote GitHub repository">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm">
              <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>
            </svg>
            <span class="btn-text">Push to GitHub</span>
          </button>
          <button id="btn-header-trash" class="btn btn-secondary btn-sm" title="View soft-deleted items & restore/purge">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
            <span class="btn-text">Trash</span> <span class="badge badge-subtle badge-trash-count" id="badge-trash-count">0</span>
          </button>
          <button id="btn-header-delete-proj" class="btn btn-danger-outline btn-sm" title="Granular deletion of project sessions, metadata, or complete erase">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              <line x1="10" y1="11" x2="10" y2="17"/>
              <line x1="14" y1="11" x2="14" y2="17"/>
            </svg>
            <span class="btn-text">Delete Project</span>
          </button>
          <button id="btn-header-resume" class="btn btn-primary btn-sm btn-resume" style="display: none;" title="Resume monitoring this project">
            <svg viewBox="0 0 24 24" fill="currentColor" class="icon-sm">
              <polygon points="5 3 19 12 5 21 5 3"/>
            </svg>
            <span class="btn-text">Resume</span>
          </button>
          <button id="btn-header-stop" class="btn btn-danger btn-sm" title="Stop monitoring worker and archive session">
            <svg viewBox="0 0 24 24" fill="currentColor" class="icon-sm">
              <rect x="6" y="6" width="12" height="12"/>
            </svg>
            <span class="btn-text">Stop</span>
          </button>
        </div>
      </header>


      <!-- Views Container -->
      <div class="view-container">
        <!-- ----------------------------------------------------------- -->
        <!-- VIEW 1: LIVE DASHBOARD                                      -->
        <!-- ----------------------------------------------------------- -->
        <section id="view-dashboard" class="tab-view active">
          <div class="dashboard-grid">
            <div class="metric-card">
              <div class="metric-header">
                <span class="metric-title">Active Workers</span>
                <span class="metric-icon green">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <polyline points="12 6 12 12 14 14"/>
                  </svg>
                </span>
              </div>
              <div class="metric-val" id="dash-active-count">0</div>
              <div class="metric-footer" id="dash-active-sub">Isolated worker processes</div>
            </div>

            <div class="metric-card">
              <div class="metric-header">
                <span class="metric-title">Prompt Bursts</span>
                <span class="metric-icon blue">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                  </svg>
                </span>
              </div>
              <div class="metric-val" id="dash-event-count">0</div>
              <div class="metric-footer" id="dash-event-sub">Coalesced 3.5s quiet windows</div>
            </div>

            <div class="metric-card">
              <div class="metric-header">
                <span class="metric-title">Patches Tracked</span>
                <span class="metric-icon purple">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14 2 14 8 20 8"/>
                    <line x1="12" y1="18" x2="12" y2="12"/>
                    <line x1="9" y1="15" x2="15" y2="15"/>
                  </svg>
                </span>
              </div>
              <div class="metric-val" id="dash-patch-count">0</div>
              <div class="metric-footer">Unified diffs with baseline</div>
            </div>

            <div class="metric-card">
              <div class="metric-header">
                <span class="metric-title">Engine Health</span>
                <span class="metric-icon amber">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
                  </svg>
                </span>
              </div>
              <div class="metric-val" id="dash-storage-tier">SQLite WAL</div>
              <div class="metric-footer" id="dash-storage-sub">Crash-resilient storage</div>
            </div>
          </div>

          <!-- Active Session Overview Card -->
          <div class="card session-overview-card">
            <div class="card-header">
              <h3>Current Session Telemetry</h3>
              <div class="card-actions" style="display: flex; gap: 8px; align-items: center;">
                <button class="btn btn-primary btn-sm btn-resume" id="btn-telemetry-resume" style="display: none;" title="Resume monitoring this project">
                  <svg viewBox="0 0 24 24" fill="currentColor" class="icon-sm">
                    <polygon points="5 3 19 12 5 21 5 3"/>
                  </svg>
                  Resume Monitoring
                </button>
                <button class="btn btn-secondary btn-sm" id="btn-export-session">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/>
                    <line x1="12" y1="15" x2="12" y2="3"/>
                  </svg>
                  Export Session JSON
                </button>
              </div>
            </div>
            <div class="card-body telemetry-grid session-details-grid">
              <div class="detail-row">
                <span class="detail-label">Project Slug:</span>
                <span class="detail-value" id="detail-slug">—</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Storage Tier:</span>
                <span class="detail-value" id="detail-tier">—</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Monitored Target:</span>
                <span class="detail-value mono" id="detail-path">—</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Session ID:</span>
                <span class="detail-value" id="detail-session-id">—</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Worker PID:</span>
                <span class="detail-value" id="detail-pid">—</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Memory Working Set:</span>
                <span class="detail-value" id="detail-memory">—</span>
              </div>
              <div class="detail-row full-width-row">
                <span class="detail-label">Active Powers:</span>
                <div class="powers-ribbon" id="powers-ribbon">
                  <span class="power-pill active" id="power-pill-edits" title="Debounced atomic diff bundling">⚡ Edits</span>
                  <span class="power-pill active" id="power-pill-reads" title="File handle read inspection">🔍 Reads</span>
                  <span class="power-pill active" id="power-pill-exec" title="Terminal execution tracking">💻 Terminal</span>
                  <span class="power-pill active" id="power-pill-git" title="Shadow Git micro-versioning">📦 Shadow Git</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Project Configuration & Specs Card -->
          <div class="card project-specs-card" id="card-project-specs">
            <div class="card-header">
              <div class="specs-header-title">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm" style="color: #38bdf8;">
                  <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/>
                  <line x1="8" y1="21" x2="16" y2="21"/>
                  <line x1="12" y1="17" x2="12" y2="21"/>
                </svg>
                <h3>Project Configuration & Specs</h3>
              </div>
              <span class="badge badge-subtle" id="specs-status-badge">Configured</span>
            </div>
            <div class="card-body">
              <div class="specs-grid">
                <div class="spec-item">
                  <span class="spec-label">Monitored Workspace Path:</span>
                  <span class="spec-value mono" id="spec-monitored-path">—</span>
                </div>
                <div class="spec-item">
                  <span class="spec-label">Monitored AI Environment:</span>
                  <span class="spec-value" id="spec-ai-profile">Google Antigravity (Native VS Code Ignored)</span>
                </div>
                <div class="spec-item">
                  <span class="spec-label">Debounce Window Setting:</span>
                  <span class="spec-value" id="spec-debounce-window">3.5s (Standard)</span>
                </div>
                <div class="spec-item">
                  <span class="spec-label">Active Powers Checklist:</span>
                  <div class="specs-checklist" id="specs-powers-checklist">
                    <span class="spec-check-pill active" id="spec-check-edits">✓ Edits & Diffs</span>
                    <span class="spec-check-pill active" id="spec-check-reads">✓ Reads (psutil)</span>
                    <span class="spec-check-pill active" id="spec-check-exec">✓ Shell Interceptor</span>
                    <span class="spec-check-pill active" id="spec-check-git">✓ Shadow Git</span>
                  </div>
                </div>
                <div class="spec-item">
                  <span class="spec-label">Linked GitHub Remote Repository:</span>
                  <div class="spec-remote-row">
                    <span class="spec-value mono" id="spec-github-remote">Not Linked</span>
                    <button class="btn btn-secondary btn-xs" id="btn-specs-push-gh" title="Push this project to GitHub">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-xs">
                        <line x1="22" y1="2" x2="11" y2="13"/>
                        <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                      </svg>
                      Push
                    </button>
                  </div>
                </div>
                <div class="spec-item">
                  <span class="spec-label">Last Push Status:</span>
                  <span class="spec-value" id="spec-last-push-status">Never pushed</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Real-Time Live Activity Stream Ticker Card -->
          <div class="card live-ticker-card" id="card-live-activity">
            <div class="card-header">
              <div class="monitor-title">
                <span class="live-dot-pulse"></span>
                <h3>Live Activity Stream (Real-Time Actions)</h3>
              </div>
              <span class="badge badge-subtle" id="live-action-count">0 actions</span>
            </div>
            <div class="card-body">
              <div class="live-ticker-feed" id="live-ticker-feed">
                <div class="ticker-empty">Listening for file modifications, file inspections, and executions…</div>
              </div>
            </div>
          </div>

          <!-- Debounce Burst Monitor Card -->
          <div class="card debounce-monitor-card" id="card-debounce-monitor">
            <div class="card-header">
              <div class="monitor-title">
                <span class="debounce-pulse-indicator idle" id="debounce-pulse"></span>
                <h3 id="debounce-monitor-title">Debounce Burst Monitor</h3>
              </div>
              <div class="monitor-header-actions" style="display: flex; gap: 8px; align-items: center;">
                <span class="debounce-status-badge idle" id="debounce-status-badge">Idle / Listening</span>
                <button type="button" class="btn btn-warning btn-xs" id="btn-seal-burst-now" title="Immediately seal and flush current burst (Lap button)" style="display: none; padding: 4px 10px; font-weight: 600;">
                  ⚡ Seal Burst Now (Lap)
                </button>
              </div>
            </div>
            <div class="card-body">
              <div class="debounce-progress-container">
                <div class="debounce-progress-bar">
                  <div class="debounce-progress-fill" id="debounce-progress-fill" style="width: 0%;"></div>
                </div>
                <div class="debounce-progress-labels">
                  <span id="debounce-status-desc">Watching for file modifications…</span>
                  <span id="debounce-countdown-text" class="mono">—</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Recent Burst Quick Preview -->
          <div class="card">
            <div class="card-header">
              <h3>Recent AI Prompt Bursts</h3>
              <div class="card-actions" style="display: flex; gap: 8px; align-items: center;">
                <button class="btn btn-secondary btn-sm" id="btn-batch-analyze-session" title="Synthesize AI summaries for all unanalyzed bursts">
                  <span class="ai-sparkle">✨</span> Batch Analyze Session
                </button>
                <button class="btn-link" id="btn-view-all-timeline">View Full Timeline &rarr;</button>
              </div>
            </div>
            <div class="card-body">
              <!-- Async Batch Progress Container -->
              <div id="batch-progress-container" class="batch-progress-box" style="display: none;">
                <div class="batch-progress-header">
                  <div class="batch-progress-info">
                    <span class="batch-pulse-spinner" id="batch-spinner"></span>
                    <strong class="batch-title" id="batch-progress-title">Synthesizing AI Intelligence...</strong>
                  </div>
                  <div class="batch-progress-right">
                    <span class="batch-pct" id="batch-progress-pct">0%</span>
                    <button type="button" class="btn btn-warning btn-xs" id="btn-batch-retry" style="display: none;">
                      ↻ Retry on Failure
                    </button>
                  </div>
                </div>
                <div class="batch-progress-track">
                  <div class="batch-progress-fill" id="batch-progress-fill" style="width: 0%;"></div>
                </div>
                <div class="batch-progress-labels">
                  <span id="batch-progress-detail">Analyzing Burst 0 of 0...</span>
                  <span id="batch-progress-time" class="mono text-dim"></span>
                </div>
              </div>

              <div id="dash-recent-events" class="event-feed-compact">
                <div class="empty-state">No events captured for this project yet.</div>
              </div>
            </div>
          </div>
        </section>

        <!-- ----------------------------------------------------------- -->
        <!-- VIEW 2: EVENT TIMELINE                                      -->
        <!-- ----------------------------------------------------------- -->
        <section id="view-timeline" class="tab-view">
          <div class="view-header-bar">
            <div>
              <h2>AI IDE Prompt Burst Timeline</h2>
              <p class="section-desc">Each card represents an atomic multi-file burst bundled after a 3.5s quiet window.</p>
            </div>
            <div class="timeline-controls">
              <div class="filter-btn-group" id="timeline-filter-group">
                <button type="button" class="filter-pill active" data-filter="all">All Events</button>
                <button type="button" class="filter-pill" data-filter="EDIT">✏️ Edits &amp; Diffs</button>
                <button type="button" class="filter-pill" data-filter="READ">👁️ Analyzed Files</button>
                <button type="button" class="filter-pill" data-filter="EXEC">💻 Terminal Executions</button>
              </div>
              <button class="btn btn-secondary btn-sm" id="btn-refresh-timeline">Refresh Events</button>
            </div>
          </div>

          <div class="timeline-container" id="timeline-container">
            <div class="empty-state">No events recorded in session.db</div>
          </div>

          <!-- Phase 4: Process Inspector Analyzed Files Panel -->
          <div class="timeline-reads-panel">
            <div class="reads-panel-header">
              <h3>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                  <circle cx="12" cy="12" r="3"/>
                </svg>
                Analyzed / Read Files Prior to Edits (psutil)
              </h3>
              <span class="badge badge-subtle" id="timeline-read-count">0</span>
            </div>
            <div class="reads-list" id="timeline-reads-list">
              <div class="empty-hint">No file inspection events recorded yet</div>
            </div>
          </div>
        </section>

        <!-- ----------------------------------------------------------- -->
        <!-- VIEW 3: PATCH DIFF VIEWER (Hierarchical 3-Step Navigation) -->
        <!-- ----------------------------------------------------------- -->
        <section id="view-diffs" class="tab-view">
          <div class="diff-viewer-layout">
            <div class="diff-sidebar">
              <!-- Level 1: Select Session -->
              <div class="diff-step-card">
                <div class="diff-step-header">
                  <span class="diff-step-num">1</span>
                  <h4>Select Session</h4>
                </div>
                <div class="diff-session-dropdown-container">
                  <select id="diff-session-select" class="form-select">
                    <option value="">(Loading sessions…)</option>
                  </select>
                </div>
              </div>

              <!-- Level 2: Select Burst Event -->
              <div class="diff-step-card">
                <div class="diff-step-header">
                  <div style="display: flex; align-items: center; gap: 8px;">
                    <span class="diff-step-num">2</span>
                    <h4>Select Burst</h4>
                  </div>
                  <span class="badge badge-subtle" id="diff-burst-count">0</span>
                </div>
                <div class="diff-burst-dropdown-container">
                  <select id="diff-burst-select" class="form-select">
                    <option value="">(Select a session above)</option>
                  </select>
                </div>
              </div>

              <!-- Level 3: Select Touched File -->
              <div class="diff-step-card">
                <div class="diff-step-header">
                  <div style="display: flex; align-items: center; gap: 8px;">
                    <span class="diff-step-num">3</span>
                    <h4>Touched Files</h4>
                  </div>
                  <span class="badge badge-subtle" id="diff-file-count">0</span>
                </div>
                <div class="diff-files-list" id="diff-files-list">
                  <div class="empty-hint">Select a burst above</div>
                </div>
              </div>
            </div>


            <div class="diff-viewer-main">
              <!-- Step 3: Diff View & AI Summary Header -->
              <div class="diff-step-card step-3-header-card">
                <div class="diff-step-header">
                  <div style="display: flex; align-items: center; gap: 8px;">
                    <span class="diff-step-num">3</span>
                    <h4>Diff View & AI Intent Summary</h4>
                  </div>
                  <div class="diff-code-stats" id="diff-code-stats">
                    <span class="diff-stat-add" id="stat-additions">+0</span>
                    <span class="diff-stat-del" id="stat-deletions">-0</span>
                  </div>
                </div>
              </div>

              <!-- Dedicated AI Intent & Summary Slot in Diff View -->
              <div class="diff-ai-banner" id="diff-ai-slot"></div>

              <div class="diff-code-header">
                <div class="diff-code-title">
                  <span class="file-icon">📄</span>
                  <span id="diff-current-file-name">No file selected</span>
                </div>
              </div>
              <div class="diff-code-container" id="diff-code-container">
                <div class="empty-state">Select an event and file to inspect the unified diff.</div>
              </div>
            </div>
          </div>
        </section>

        <!-- ----------------------------------------------------------- -->
        <!-- VIEW 4: TERMINAL & COMMANDS (Phase 4)                       -->
        <!-- ----------------------------------------------------------- -->
        <section id="view-terminal" class="tab-view">
          <div class="view-header-bar">
            <div>
              <h2>Terminal Execution Audit</h2>
              <p class="section-desc">Audit trail of shell commands, processes, exit codes, and timestamps.</p>
            </div>
            <div class="terminal-controls">
              <button class="btn btn-secondary btn-sm" id="btn-refresh-terminal">Refresh Commands</button>
            </div>
          </div>
          <div class="terminal-container" id="terminal-feed-container">
            <div class="empty-state">No terminal commands recorded yet.</div>
          </div>
        </section>

        <!-- ----------------------------------------------------------- -->
        <!-- VIEW 5: WORKER LOGS                                         -->
        <!-- ----------------------------------------------------------- -->
        <section id="view-logs" class="tab-view">
          <div class="logs-header-bar">
            <div>
              <h2>Worker Daemon Log Stream</h2>
              <span class="section-desc">Reading from <code>worker.log</code> (last 150 lines)</span>
            </div>
            <div class="logs-controls">
              <label class="checkbox-label">
                <input type="checkbox" id="chk-autoscroll" checked>
                Auto-scroll
              </label>
              <button class="btn btn-secondary btn-sm" id="btn-copy-logs">Copy Logs</button>
              <button class="btn btn-secondary btn-sm" id="btn-refresh-logs">Refresh</button>
            </div>
          </div>

          <div class="logs-terminal" id="logs-terminal">
            <div class="log-line">Connecting to worker log stream…</div>
          </div>
        </section>
      </div>
    </main>
  </div>

  <!-- ============================================================= -->
  <!-- MODAL: NEW SESSION LAUNCHER                                   -->
  <!-- ============================================================= -->
  <div class="modal-backdrop" id="modal-new-session">
    <div class="modal-box modal-content" id="new-session-modal">
      <div class="modal-header">
        <div class="modal-title">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon green">
            <circle cx="12" cy="12" r="10"/>
            <polyline points="12 6 12 12 14 14"/>
          </svg>
          <h3>Launch New Monitoring Session</h3>
        </div>
        <button class="modal-close" id="modal-close-btn">&times;</button>
      </div>

      <form id="form-new-session" class="modal-body">
        <div class="form-group">
          <label for="inp-project-name">Project Name <span class="required">*</span></label>
          <input type="text" id="inp-project-name" class="form-control" placeholder="e.g. backend_service" required>
          <span class="form-help">Will be used as directory slug: <code id="slug-preview">backend_service</code></span>
        </div>

        <div class="form-group">
          <label for="inp-target-path">Workspace Target Path <span class="required">*</span></label>
          <input type="text" id="inp-target-path" class="form-control mono" placeholder="e.g. D:/Projects/backend_service" required>
          <span class="form-help">Absolute path to the workspace directory to observe</span>
        </div>

        <div class="form-check-group">
          <label class="form-checkbox">
            <input type="checkbox" id="chk-scaffold" checked>
            <span><strong>Auto-scaffold directory:</strong> Create target path automatically if it doesn't exist</span>
          </label>
        </div>

        <div class="form-group">
          <div class="slider-header">
            <label for="rng-debounce">Debounce Quiet Window:</label>
            <span class="slider-val" id="debounce-val-display">3.5s <span class="badge badge-recommended">Fast (Recommended)</span></span>
          </div>
          <input type="range" id="rng-debounce" min="1.0" max="1800.0" step="0.5" value="3.5" class="form-range">
          <div class="slider-presets-row" style="display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px;">
            <button type="button" class="btn btn-secondary btn-xs btn-debounce-preset" data-val="3.5">3.5s (Fast)</button>
            <button type="button" class="btn btn-secondary btn-xs btn-debounce-preset" data-val="10">10s (Medium)</button>
            <button type="button" class="btn btn-secondary btn-xs btn-debounce-preset" data-val="60">60s (1m)</button>
            <button type="button" class="btn btn-secondary btn-xs btn-debounce-preset" data-val="300">300s (5m)</button>
            <button type="button" class="btn btn-secondary btn-xs btn-debounce-preset" data-val="600">600s (10m)</button>
            <button type="button" class="btn btn-secondary btn-xs btn-debounce-preset" data-val="1800">1800s (30m)</button>
          </div>
          <span class="form-help">Coalesces rapid multi-file edits into a single atomic burst event (1.0s to 1800.0s / 30m)</span>
        </div>

        <div class="form-group">
          <label for="sel-ide-profile">Monitored AI Environment (Process Isolation)</label>
          <select id="sel-ide-profile" class="form-control">
            <option value="antigravity" selected>Google Antigravity (Default)</option>
            <option value="cursor">Cursor AI</option>
            <option value="windsurf">Windsurf / Codeium</option>
            <option value="claude_code">Claude Code CLI</option>
            <option value="custom">Custom AI Environment...</option>
          </select>
          <span class="form-help">Strictly restricts file read inspection to the chosen AI tool tree. Native Microsoft VS Code background tasks are ignored.</span>
        </div>

        <div class="form-group hidden" id="group-custom-ide-marker">
          <label for="inp-custom-ide-marker">Custom Executable Name or Process Marker</label>
          <input type="text" id="inp-custom-ide-marker" class="form-control mono" placeholder="e.g. my-custom-ai.exe or /opt/custom_ai">
          <span class="form-help">Substring or executable name to match in the process ancestry tree.</span>
        </div>

        <div class="form-group">
          <label>Selective Feature Powers (Modular Toggles)</label>
          <div class="features-checklist">
            <label class="form-checkbox">
              <input type="checkbox" id="chk-power-edits" checked disabled>
              <span><strong>Atomic Burst Diffing:</strong> 3.5s quiet window unified diffs (Core)</span>
            </label>
            <label class="form-checkbox">
              <input type="checkbox" id="chk-power-reads" checked>
              <span><strong>File Read Inspection:</strong> Track files analyzed by IDEs via process handles</span>
            </label>
            <label class="form-checkbox">
              <input type="checkbox" id="chk-power-exec" checked>
              <span><strong>Terminal Command Auditing:</strong> Log executed shell commands and exit codes</span>
            </label>
            <label class="form-checkbox">
              <input type="checkbox" id="chk-power-shadow-git" checked>
              <span><strong>Shadow Git Micro-Versioning:</strong> Automatic isolated git commits per burst</span>
            </label>
            <label class="form-checkbox">
              <input type="checkbox" id="chk-power-git-init">
              <span><strong>Initialize Primary Git:</strong> Run git init in target workspace if no .git exists</span>
            </label>
          </div>
        </div>

        <div class="preflight-card">
          <div class="preflight-title">Pre-flight Checklist</div>
          <ul class="preflight-list">
            <li>✔ Isolated background worker process with dedicated process group</li>
            <li>✔ SQLite WAL mode storage with ACID resilience against crashes</li>
            <li>✔ Recursive watchdog filesystem observer with binary & noise filtering</li>
            <li>✔ Baseline snapshotting with diff computation and entrypoint detection</li>
          </ul>
        </div>

        <div id="modal-error" class="alert-box error hidden"></div>

        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" id="modal-cancel-btn">Cancel</button>
          <button type="submit" class="btn btn-primary" id="modal-submit-btn">
            <span class="btn-spinner hidden" id="modal-spinner"></span>
            Launch Worker
          </button>
        </div>
      </form>
    </div>
  </div>

  <!-- ============================================================= -->
  <!-- MODAL: GITHUB SETTINGS & CREDENTIALS                         -->
  <!-- ============================================================= -->
  <div class="modal-backdrop" id="modal-git-settings">
    <div class="modal-box modal-content" id="git-settings-modal" style="max-width: 540px;">
      <div class="modal-header">
        <div class="modal-title">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon" style="color: #38bdf8;">
            <circle cx="12" cy="12" r="3"/>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
          </svg>
          <h3>GitHub Credentials & Push Settings</h3>
        </div>
        <button class="modal-close" id="modal-git-settings-close">&times;</button>
      </div>

      <form id="form-git-settings" class="modal-body">
        <div class="form-group">
          <label for="inp-git-username">GitHub Username / Org</label>
          <input type="text" id="inp-git-username" class="form-control" placeholder="e.g. octocat">
          <span class="form-help">Used for authenticated remote operations</span>
        </div>

        <div class="form-group">
          <label for="inp-git-token">Personal Access Token (PAT)</label>
          <input type="password" id="inp-git-token" class="form-control mono" placeholder="ghp_••••••••••••••••••••••••••••••••••••">
          <span class="form-help">Stored locally in <code>ai_data/git_config.json</code>. Masked across logs and APIs.</span>
        </div>

        <div class="form-group-row" style="display: flex; gap: 10px;">
          <div class="form-group" style="flex: 1;">
            <label for="inp-git-default-remote">Default Remote</label>
            <input type="text" id="inp-git-default-remote" class="form-control" placeholder="origin">
          </div>
          <div class="form-group" style="flex: 1;">
            <label for="inp-git-default-branch">Default Branch</label>
            <input type="text" id="inp-git-default-branch" class="form-control" placeholder="main">
          </div>
        </div>

        <div class="form-check-group">
          <label class="form-checkbox">
            <input type="checkbox" id="chk-git-auto-changelog" checked>
            <span><strong>Auto-synthesize AI Changelog:</strong> Generate Conventional Commits and prepend entries to <code>CHANGELOG.md</code></span>
          </label>
        </div>

        <!-- Connection Test Box -->
        <div class="git-test-card" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-card); border-radius: var(--radius-sm); padding: 10px 12px; margin-top: 4px;">
          <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px;">
            <label for="inp-git-test-url" style="font-size: 0.76rem; font-weight: 600; color: #38bdf8;">Remote Repository URL for Test:</label>
            <button type="button" class="btn btn-secondary btn-sm" id="btn-test-git-connection" style="padding: 2px 10px; font-size: 0.74rem;">Test Connection</button>
          </div>
          <input type="text" id="inp-git-test-url" class="form-control mono" style="font-size: 0.78rem;" placeholder="https://github.com/username/repository.git">
          <div id="git-test-status" class="git-test-badge hidden" style="margin-top: 6px; font-size: 0.76rem; padding: 4px 8px; border-radius: 4px;"></div>
        </div>

        <div class="modal-footer" style="margin-top: 8px;">
          <button type="button" class="btn btn-secondary" id="modal-git-settings-cancel">Cancel</button>
          <button type="submit" class="btn btn-primary" id="btn-save-git-config">Save Settings</button>
        </div>
      </form>
    </div>
  </div>

  <!-- ============================================================= -->
  <!-- MODAL: MILESTONE SYNC & CHANGELOG REVIEW                     -->
  <!-- ============================================================= -->
  <div class="modal-backdrop" id="modal-sync-review">
    <div class="modal-box modal-content" id="sync-review-modal" style="max-width: 680px;">
      <div class="modal-header">
        <div class="modal-title">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon" style="color: #a855f7;">
            <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>
          </svg>
          <h3>Push to GitHub — Milestone Review</h3>
        </div>
        <button class="modal-close" id="modal-sync-review-close">&times;</button>
      </div>

      <div class="modal-body" id="sync-review-body">
        <div class="sync-review-notice" style="display: flex; align-items: center; justify-content: space-between; background: rgba(168, 85, 247, 0.1); border: 1px solid rgba(168, 85, 247, 0.25); border-radius: var(--radius-sm); padding: 8px 12px; font-size: 0.78rem;">
          <span style="color: #c084fc;">AI synthesized Conventional Commit & Changelog from <strong id="sync-events-count">0</strong> bursts</span>
          <span class="ai-provider-badge" id="sync-provider-badge">AI SYNTHESIZER</span>
        </div>

        <div class="form-group">
          <label for="inp-sync-commit-title">Conventional Commit Title <span class="required">*</span></label>
          <input type="text" id="inp-sync-commit-title" class="form-control" placeholder="feat(core): implement prompt burst debouncing">
        </div>

        <div class="form-group">
          <label for="inp-sync-commit-body">Commit Body / Bulleted Highlights</label>
          <textarea id="inp-sync-commit-body" class="form-control mono" rows="3" placeholder="- Added debouncer&#10;- Recorded file handles"></textarea>
        </div>

        <div class="form-group">
          <div style="display: flex; align-items: center; justify-content: space-between;">
            <label for="inp-sync-changelog-content">Changelog Entry (prepended to CHANGELOG.md)</label>
            <span style="font-size: 0.72rem; color: var(--text-dim);">Markdown format</span>
          </div>
          <textarea id="inp-sync-changelog-content" class="form-control mono" rows="6" style="font-size: 0.78rem;"></textarea>
        </div>

        <div class="form-group-row" style="display: flex; gap: 10px;">
          <div class="form-group" style="flex: 2;">
            <label for="inp-sync-remote">Remote URL or Name</label>
            <input type="text" id="inp-sync-remote" class="form-control mono" placeholder="origin">
          </div>
          <div class="form-group" style="flex: 1;">
            <label for="inp-sync-branch">Branch</label>
            <input type="text" id="inp-sync-branch" class="form-control" placeholder="main">
          </div>
        </div>

        <div class="form-check-group" style="margin-top: 6px; margin-bottom: 8px;">
          <label class="form-checkbox">
            <input type="checkbox" id="chk-sync-auto-create-repo" checked>
            <span><strong>Auto-create repository on GitHub if missing:</strong> Automatically create this repo on your GitHub account via API before pushing</span>
          </label>
        </div>

        <!-- GitHub Push 3-Step Progress Stepper -->
        <div class="sync-stepper-container hidden" id="sync-stepper-container">
          <div class="stepper-progress-bar">
            <div class="stepper-progress-fill" id="sync-stepper-fill" style="width: 0%;"></div>
          </div>
          <div class="stepper-steps">
            <div class="stepper-step" id="step-1">
              <span class="step-num">1</span>
              <span class="step-label">Changelog</span>
            </div>
            <div class="stepper-step" id="step-2">
              <span class="step-num">2</span>
              <span class="step-label">Remote Repo</span>
            </div>
            <div class="stepper-step" id="step-3">
              <span class="step-num">3</span>
              <span class="step-label">Git Push</span>
            </div>
          </div>
          <div class="stepper-status-msg" id="sync-stepper-msg">Ready to push</div>
        </div>

        <div id="sync-error" class="alert-box error hidden"></div>

        <div class="modal-footer" style="margin-top: 8px;">
          <button type="button" class="btn btn-secondary" id="modal-sync-cancel">Cancel</button>
          <button type="button" class="btn btn-primary" id="btn-confirm-sync-push" style="background: linear-gradient(135deg, #a855f7, #6366f1);">
            <span class="btn-spinner hidden" id="sync-spinner"></span>
            Confirm & Push to Remote
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- ============================================================= -->
  <!-- MODAL: AI MODEL CONFIGURATION                                -->
  <!-- ============================================================= -->
  <div class="modal-backdrop" id="modal-ai-settings">
    <div class="modal-box modal-content" id="ai-settings-modal" style="max-width: 580px;">
      <div class="modal-header">
        <div class="modal-title">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon" style="color: #a855f7;">
            <circle cx="12" cy="12" r="10"/>
            <path d="M12 6v6l4 2"/>
          </svg>
          <h3>AI Intelligence & Model Settings</h3>
        </div>
        <button class="modal-close" id="modal-ai-settings-close">&times;</button>
      </div>

      <form id="form-ai-settings" class="modal-body">
        <div class="form-group">
          <label for="sel-ai-provider">Active AI Intelligence Provider</label>
          <select id="sel-ai-provider" class="form-select">
            <option value="heuristic">Zero-AI Heuristic Mode (Deterministic Offline, Zero Load)</option>
            <option value="ollama">Local Offline LLM (Ollama)</option>
            <option value="gemini">Google Gemini API (Cloud)</option>
          </select>
          <span class="form-help">Select the intelligence engine used for patch summarization and changelogs.</span>
        </div>

        <!-- Provider: Ollama -->
        <div class="provider-config-box" id="box-provider-ollama">
          <div class="provider-box-header">
            <span class="provider-box-title">Ollama Configuration (Local Offline)</span>
            <button type="button" class="btn btn-secondary btn-sm" id="btn-test-ollama">Test Ollama Connection</button>
          </div>
          <div class="form-group">
            <label for="inp-ollama-url">Base URL</label>
            <input type="text" id="inp-ollama-url" class="form-control mono" placeholder="http://127.0.0.1:11434">
          </div>
          <div class="form-group">
            <label for="inp-ollama-model">Model Name</label>
            <input type="text" id="inp-ollama-model" class="form-control mono" placeholder="qwen2.5-coder:7b" list="ollama-model-suggestions">
            <datalist id="ollama-model-suggestions">
              <option value="qwen2.5-coder:7b">
              <option value="qwen2.5-coder:14b">
              <option value="llama3.1:8b">
              <option value="codellama:7b">
              <option value="mistral:latest">
            </datalist>
          </div>
          <div id="ollama-test-status" class="test-status-badge hidden"></div>
        </div>

        <!-- Provider: Gemini -->
        <div class="provider-config-box" id="box-provider-gemini">
          <div class="provider-box-header">
            <span class="provider-box-title">Gemini API Configuration (Cloud)</span>
            <button type="button" class="btn btn-secondary btn-sm" id="btn-test-gemini">Test Gemini Key</button>
          </div>
          <div class="form-group">
            <label for="sel-gemini-key-vault">Saved Keys (Vault)</label>
            <div class="key-vault-selector-row">
              <select id="sel-gemini-key-vault" class="form-select">
                <option value="__new__">+ Add / Paste New Key</option>
              </select>
              <button type="button" class="btn-icon-danger" id="btn-delete-gemini-key" title="Remove selected key from vault" style="display: none;">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                </svg>
              </button>
            </div>
          </div>
          <div class="form-group" id="group-gemini-input-key">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
              <label for="inp-gemini-key" id="lbl-gemini-key" style="margin-bottom: 0;">Gemini API Key</label>
              <span id="gemini-active-key-info" class="badge badge-subtle" style="font-family: monospace; font-size: 0.72rem; color: #a855f7;">Active Key: (not set)</span>
            </div>
            <input type="password" id="inp-gemini-key" class="form-control mono" placeholder="AIzaSy•••••••••••••••••••••••••••••••">
            <span class="form-help" id="help-gemini-key">Precedence: <code>models_config.json</code> &rarr; <code>.env</code> &rarr; system environment.</span>
          </div>
          <div class="form-group">
            <label for="sel-gemini-model">Gemini Model</label>
            <select id="sel-gemini-model" class="form-select">
              <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite (Fast Lite - Recommended - 500 RPD / 15 RPM)</option>
              <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite (Flash Lite - 500 RPD / 15 RPM)</option>
              <option value="gemini-3.8-flash">gemini-3.8-flash (Gemini 3.8 Flash - 20 RPD / 5 RPM)</option>
              <option value="gemini-3.7-flash">gemini-3.7-flash (Gemini 3.7 Flash - 20 RPD / 5 RPM)</option>
              <option value="gemini-2.5-flash">gemini-2.5-flash (Gemini 2.5 Flash - 20 RPD / 5 RPM)</option>
            </select>
          </div>
          <div id="gemini-test-status" class="test-status-badge hidden"></div>
        </div>

        <!-- Provider: Heuristic Notice -->
        <div class="provider-config-box" id="box-provider-heuristic">
          <div class="heuristic-notice-card">
            <span class="heuristic-icon">⚡</span>
            <div>
              <strong>Zero-AI Heuristic Mode:</strong>
              <p style="margin: 4px 0 0; font-size: 0.78rem; color: var(--text-dim);">
                Produces deterministic, semantic changelogs and burst summaries purely from git diff file patterns, additions, and deletions. No network connection, no GPU/RAM overhead, zero cost.
              </p>
            </div>
          </div>
        </div>

        <div class="modal-footer" style="margin-top: 12px;">
          <button type="button" class="btn btn-secondary" id="modal-ai-settings-cancel">Cancel</button>
          <button type="submit" class="btn btn-primary" id="btn-save-ai-config">Save AI Settings</button>
        </div>
      </form>
    </div>
  </div>

  <!-- ============================================================= -->
  <!-- MODAL: PROJECT DELETION (Granular 5 Scopes)                  -->
  <!-- ============================================================= -->
  <div class="modal-backdrop" id="modal-delete-project">
    <div class="modal-box modal-content" id="delete-project-modal" style="max-width: 640px;">
      <div class="modal-header">
        <div class="modal-title">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon" style="color: #ef4444;">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            <line x1="10" y1="11" x2="10" y2="17"/>
            <line x1="14" y1="11" x2="14" y2="17"/>
          </svg>
          <h3>Delete Project Data &mdash; <span id="del-modal-project-name" class="mono text-highlight"></span></h3>
        </div>
        <button class="modal-close" id="modal-delete-project-close">&times;</button>
      </div>

      <div class="modal-body">
        <div class="alert-box warning" style="margin-bottom: 16px;">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm" style="flex-shrink: 0;">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
            <line x1="12" y1="9" x2="12" y2="13"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <div>
            <strong>Soft Deletion Active:</strong> Deleted project artifacts are moved to the <strong>Trash Bin</strong> with a manifest, and can be restored at any time.
          </div>
        </div>

        <div class="delete-scope-list">
          <!-- Scope 1: Selective Bursts -->
          <label class="scope-option">
            <input type="radio" name="delete-scope" value="selective_bursts">
            <div class="scope-info">
              <div class="scope-title">
                <strong>1. Selective Prompt Bursts</strong>
                <span class="scope-badge">Granular</span>
              </div>
              <p class="scope-desc">Pick and remove individual bursts/diffs from the timeline. Unselected bursts remain intact.</p>
            </div>
          </label>

          <!-- Selective Bursts Checklist Container -->
          <div id="delete-bursts-container" class="selective-bursts-box" style="display: none;">
            <div class="selective-bursts-header">
              <span>Select bursts to delete:</span>
              <button type="button" class="btn-link" id="btn-select-all-bursts">Select All</button>
            </div>
            <div id="delete-bursts-list" class="selective-bursts-list">
              <!-- Dynamically populated with burst checkboxes -->
            </div>
          </div>

          <!-- Scope 2: Metadata Only -->
          <label class="scope-option">
            <input type="radio" name="delete-scope" value="metadata_only">
            <div class="scope-info">
              <div class="scope-title">
                <strong>2. Project Metadata Only</strong>
                <span class="scope-badge">Config Only</span>
              </div>
              <p class="scope-desc">Wipe project remote link, branch, push commit states, and runtime cache. Events are preserved.</p>
            </div>
          </label>

          <!-- Scope 3: Local Session -->
          <label class="scope-option selected">
            <input type="radio" name="delete-scope" value="local_session" checked>
            <div class="scope-info">
              <div class="scope-title">
                <strong>3. Local Monitoring Session (Recommended)</strong>
                <span class="scope-badge">Full Session</span>
              </div>
              <p class="scope-desc">Move <code>session.db</code>, patch diffs, baselines, and worker logs to trash. Cleans the timeline.</p>
            </div>
          </label>

          <!-- Scope 4: Session + Shadow Git -->
          <label class="scope-option">
            <input type="radio" name="delete-scope" value="session_and_shadow_git">
            <div class="scope-info">
              <div class="scope-title">
                <strong>4. Session &amp; Shadow Git</strong>
                <span class="scope-badge">Deep Clean</span>
              </div>
              <p class="scope-desc">Move session data and the local <code>shadow_git/</code> micro-commit repository into trash.</p>
            </div>
          </label>

          <!-- Scope 5: Complete Erase -->
          <label class="scope-option danger-scope">
            <input type="radio" name="delete-scope" value="complete_erase">
            <div class="scope-info">
              <div class="scope-title">
                <strong>5. Complete Project Erase</strong>
                <span class="scope-badge badge-danger">Total Wipe</span>
              </div>
              <p class="scope-desc">Move all local project engine folders (current, last_run, history) to trash.</p>
            </div>
          </label>

          <!-- GitHub Remote Option (for Complete Erase) -->
          <div id="delete-remote-option" class="delete-remote-box" style="display: none;">
            <label class="checkbox-label" style="color: #f87171;">
              <input type="checkbox" id="chk-delete-remote-github">
              <span><strong>Permanently delete remote repository on GitHub</strong> via REST API (if credentials configured)</span>
            </label>
            <span class="form-help" style="margin-left: 24px; color: #ef4444;">Warning: GitHub deletion is permanent and cannot be undone via Trash Bin.</span>
          </div>
        </div>

        <div id="delete-project-error" class="alert-box error hidden"></div>

        <div class="modal-footer" style="margin-top: 16px;">
          <button type="button" class="btn btn-secondary" id="modal-delete-project-cancel">Cancel</button>
          <button type="button" class="btn btn-danger" id="btn-confirm-delete-project">
            <span class="btn-spinner hidden" id="delete-project-spinner"></span>
            Move to Trash (Soft Delete)
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- ============================================================= -->
  <!-- MODAL: TRASH BIN (Soft-Delete & Restore Management)           -->
  <!-- ============================================================= -->
  <div class="modal-backdrop" id="modal-trash-bin">
    <div class="modal-box modal-content" id="trash-bin-modal" style="max-width: 860px;">
      <div class="modal-header">
        <div class="modal-title">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon" style="color: #f59e0b;">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
          </svg>
          <h3>Trash Bin &mdash; Soft-Deleted Projects &amp; Bursts</h3>
        </div>
        <div style="display: flex; gap: 8px; align-items: center;">
          <button type="button" class="btn btn-danger-outline btn-sm" id="btn-empty-trash" title="Permanently delete all items from disk">
            Empty Trash Bin
          </button>
          <button class="modal-close" id="modal-trash-bin-close">&times;</button>
        </div>
      </div>

      <div class="modal-body" style="padding: 16px 20px;">
        <p class="section-desc" style="margin-bottom: 12px;">
          All items deleted from the engine are kept here. Restore any item to instantly put it back, or purge permanently.
        </p>

        <div id="trash-bin-items-container" class="trash-items-list">
          <div class="empty-state">Loading trash bin…</div>
        </div>

        <div id="trash-bin-error" class="alert-box error hidden" style="margin-top: 12px;"></div>
      </div>
    </div>
  </div>

  <!-- Floating Global Job & Rate-Limit Progress Tray -->
  <div id="global-job-tray" class="global-job-tray hidden" aria-live="polite">
    <div class="tray-header">
      <div class="tray-title-area">
        <span class="tray-spinner" id="tray-spinner"></span>
        <span class="tray-title" id="tray-title">AI Batch Synthesis</span>
      </div>
      <button class="tray-close-btn" id="tray-close-btn" title="Dismiss tray" aria-label="Close tray">&times;</button>
    </div>
    <div class="tray-body">
      <div class="tray-project" id="tray-project-name">Project: &mdash;</div>
      <div class="tray-progress-wrap">
        <div class="tray-progress-bar" id="tray-progress-bar" style="width: 0%;"></div>
      </div>
      <div class="tray-stats-row">
        <span class="tray-counts" id="tray-counts">Analyzed 0 / 0 bursts</span>
        <span class="tray-percent" id="tray-percent">0%</span>
      </div>
      <div class="tray-cooldown-badge hidden" id="tray-cooldown-badge">
        <span class="cooldown-icon">⏳</span>
        <span id="tray-cooldown-text">Rate-Limit Cooldown: 60s remaining</span>
      </div>
    </div>
  </div>

  <!-- Toast Notification Container -->
  <div id="toast-container" class="toast-container"></div>

  <script src="app.js"></script>
</body>
</html>
```

---

### [27/27] File: `web\style.css`
- **Lines:** 3387 | **Size:** 66.72 KB | **Type:** css

```css
/* ==========================================================================
   SPD Analysis Engine — Web Control Portal Stylesheet
   Dark Slate/Zinc Palette with Emerald & Sky Accents
   ========================================================================== */

:root {
  --bg-app: #090d16;
  --bg-sidebar: #0f172a;
  --bg-header: #0f172a;
  --bg-card: #131c31;
  --bg-card-hover: #18243e;
  --bg-input: #0b1325;
  --bg-terminal: #050811;

  --border-subtle: #1e293b;
  --border-card: #202d48;
  --border-focus: #38bdf8;

  --text-main: #f8fafc;
  --text-muted: #94a3b8;
  --text-dim: #64748b;

  --accent-green: #22c55e;
  --accent-green-bg: rgba(34, 197, 94, 0.12);
  --accent-green-border: rgba(34, 197, 94, 0.35);

  --accent-blue: #38bdf8;
  --accent-blue-bg: rgba(56, 189, 248, 0.12);

  --accent-red: #ef4444;
  --accent-red-bg: rgba(239, 68, 68, 0.12);

  --accent-amber: #f59e0b;
  --accent-amber-bg: rgba(245, 158, 11, 0.12);

  --accent-purple: #a855f7;
  --accent-purple-bg: rgba(168, 85, 247, 0.12);

  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;

  --sidebar-w: 280px;
  --header-h: 68px;
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: var(--font-sans);
  background-color: var(--bg-app);
  color: var(--text-main);
  line-height: 1.5;
  overflow: hidden;
  height: 100vh;
}

/* ==========================================================================
   APP LAYOUT: Left-Sidebar + Main
   ========================================================================== */

.app-layout {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}

/* ==========================================================================
   SIDEBAR
   ========================================================================== */

.sidebar {
  width: var(--sidebar-w);
  min-width: var(--sidebar-w);
  background-color: var(--bg-sidebar);
  border-right: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  height: 100vh;
  z-index: 20;
  transition: width 0.22s cubic-bezier(0.4, 0, 0.2, 1), min-width 0.22s cubic-bezier(0.4, 0, 0.2, 1);
}

.sidebar.collapsed {
  width: 64px;
  min-width: 64px;
}

.sidebar.collapsed .brand-text,
.sidebar.collapsed .sidebar-action,
.sidebar.collapsed .nav-section-title,
.sidebar.collapsed .sidebar-projects,
.sidebar.collapsed .nav-label,
.sidebar.collapsed .badge {
  display: none !important;
}

.sidebar.collapsed .sidebar-brand {
  padding: 16px 8px;
  justify-content: center;
  position: relative;
}

.sidebar.collapsed .brand-icon {
  width: 32px;
  height: 32px;
}

.sidebar.collapsed .sidebar-toggle-btn {
  margin-left: 0;
  position: absolute;
  bottom: -13px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 25;
  background: var(--bg-card);
  border: 1px solid var(--border-card);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
  width: 22px;
  height: 22px;
  font-size: 0.65rem;
}

.sidebar.collapsed .sidebar-nav {
  padding: 20px 6px 12px;
  gap: 6px;
}

.sidebar.collapsed .nav-item {
  justify-content: center;
  padding: 10px 0;
  width: 100%;
}

.sidebar.collapsed .nav-item .icon {
  margin: 0;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 18px 16px;
  border-bottom: 1px solid var(--border-subtle);
}

.sidebar-toggle-btn {
  margin-left: auto;
  width: 26px;
  height: 26px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 0.72rem;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.sidebar-toggle-btn:hover {
  background-color: var(--border-subtle);
  color: #ffffff;
}

.brand-icon {
  width: 38px;
  height: 38px;
  background: linear-gradient(135deg, #10b981, #0284c7);
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ffffff;
  box-shadow: 0 4px 12px rgba(16, 185, 129, 0.25);
  flex-shrink: 0;
}

.brand-icon svg {
  width: 22px;
  height: 22px;
}

.brand-text {
  overflow: hidden;
  white-space: nowrap;
}

.brand-text h2 {
  font-size: 1.05rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #ffffff;
}

.brand-sub {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-dim);
  display: block;
}

.sidebar-action {
  padding: 14px 16px 10px;
}

.nav-section-title {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-dim);
  font-weight: 600;
  padding: 8px 18px 4px;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 10px 14px;
  border-bottom: 1px solid var(--border-subtle);
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 0.88rem;
  font-weight: 500;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
  width: 100%;
}

.nav-item:hover {
  background-color: rgba(255, 255, 255, 0.04);
  color: var(--text-main);
}

.nav-item.active {
  background-color: var(--bg-card);
  color: #38bdf8;
  border-color: var(--border-card);
  font-weight: 600;
}

.nav-item .icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.badge {
  font-size: 0.72rem;
  padding: 2px 7px;
  border-radius: 9999px;
  font-weight: 600;
  margin-left: auto;
}

.badge-subtle {
  background-color: var(--border-subtle);
  color: var(--text-muted);
}

/* Projects Explorer */

.sidebar-projects {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding-top: 10px;
}

.projects-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-right: 14px;
}

.project-search {
  padding: 6px 12px 10px;
}

.project-search input {
  width: 100%;
  padding: 6px 10px;
  background-color: var(--bg-input);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-main);
  font-size: 0.8rem;
}

.project-search input:focus {
  outline: none;
  border-color: var(--accent-blue);
}

.projects-list-container {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tier-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tier-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--text-dim);
  padding: 4px 8px;
}

.tier-count {
  background-color: rgba(255, 255, 255, 0.05);
  padding: 1px 6px;
  border-radius: 4px;
}

.tier-items {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.project-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  color: var(--text-muted);
  font-size: 0.83rem;
  transition: all 0.15s ease;
  width: 100%;
  text-align: left;
}

.project-item:hover {
  background-color: rgba(255, 255, 255, 0.03);
  color: var(--text-main);
}

.project-item.active {
  background-color: var(--bg-card);
  border-color: var(--border-card);
  color: #ffffff;
  font-weight: 600;
}

.project-item-left {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background-color: var(--accent-green);
  box-shadow: 0 0 8px var(--accent-green);
  flex-shrink: 0;
}

.static-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background-color: var(--text-dim);
  flex-shrink: 0;
}

.empty-hint {
  font-size: 0.75rem;
  color: var(--text-dim);
  padding: 4px 8px;
  font-style: italic;
}

/* Sidebar Footer */

.sidebar-footer {
  padding: 10px 14px;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.74rem;
  white-space: nowrap;
  overflow: hidden;
  gap: 8px;
  flex-shrink: 0;
}

.connection-status {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 1;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.status-dot.connected {
  background-color: var(--accent-green);
  box-shadow: 0 0 6px var(--accent-green);
}

.status-dot.disconnected {
  background-color: var(--accent-red);
}

.app-version {
  color: var(--text-dim);
}

/* ==========================================================================
   MAIN CONTENT AREA
   ========================================================================== */

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background-color: var(--bg-app);
}

/* Top Header Bar */

/* Top Header Bar */

.top-header, .header-bar {
  min-height: 68px;
  background-color: var(--bg-header);
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 24px;
  z-index: 10;
  flex-wrap: nowrap;
  overflow-x: hidden;
  overflow-y: hidden;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 220px;
  max-width: 340px;
  flex-shrink: 0;
  overflow: hidden;
}

.project-indicator {
  display: flex;
  align-items: center;
  gap: 10px;
  overflow: hidden;
}

.project-tier-pill {
  font-size: 0.68rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 2px 7px;
  border-radius: 4px;
  letter-spacing: 0.05em;
  background-color: var(--accent-green-bg);
  color: var(--accent-green);
  border: 1px solid var(--accent-green-border);
  flex-shrink: 0;
}

.project-tier-pill.stopped {
  background-color: rgba(148, 163, 184, 0.12);
  color: var(--text-muted);
  border-color: rgba(148, 163, 184, 0.2);
}

.project-tier-pill.interrupted {
  background-color: rgba(239, 68, 68, 0.18);
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.4);
}

.header-project-name {
  font-size: 1.15rem;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: -0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-target-path {
  font-size: 0.78rem;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 320px;
  overflow: hidden;
  white-space: nowrap;
}

.path-label {
  color: var(--text-dim);
  flex-shrink: 0;
}

.path-val {
  font-family: var(--font-mono);
  font-size: 0.76rem;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: inline-block;
  max-width: 260px;
  cursor: help;
}

/* Header Center: Fixed Telemetry */

.header-center {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.telemetry-pill {
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 66px;
  flex-shrink: 0;
}

.tel-label {
  font-size: 0.6rem;
  font-weight: 700;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.tel-val {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  font-weight: 600;
  color: #ffffff;
  white-space: nowrap;
}

/* Header Right: Fixed Toolbar */

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  white-space: nowrap;
  margin-left: auto;
}

.header-right .btn {
  flex-shrink: 0;
  white-space: nowrap;
}


/* View Container */

.view-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.tab-view {
  display: none;
  animation: fadeIn 0.15s ease-in-out;
}

.tab-view.active {
  display: block;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ==========================================================================
   VIEW 1: DASHBOARD
   ========================================================================== */

.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.metric-card {
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-md);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: transform 0.15s ease, border-color 0.15s ease;
}

.metric-card:hover {
  transform: translateY(-2px);
  border-color: rgba(56, 189, 248, 0.3);
}

.metric-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.metric-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text-muted);
}

.metric-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
}

.metric-icon svg {
  width: 18px;
  height: 18px;
}

.metric-icon.green { background: var(--accent-green-bg); color: var(--accent-green); }
.metric-icon.blue { background: var(--accent-blue-bg); color: var(--accent-blue); }
.metric-icon.purple { background: var(--accent-purple-bg); color: var(--accent-purple); }
.metric-icon.amber { background: var(--accent-amber-bg); color: var(--accent-amber); }

.metric-val {
  font-size: 1.8rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #ffffff;
}

.metric-footer {
  font-size: 0.75rem;
  color: var(--text-dim);
}

/* Cards */

.card {
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-md);
  margin-bottom: 20px;
  overflow: hidden;
}

.card-header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-card);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-header h3 {
  font-size: 1rem;
  font-weight: 600;
  color: #ffffff;
}

.card-body {
  padding: 20px;
}

.telemetry-grid,
.session-details-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 16px 20px;
  width: 100%;
}

.detail-row,
.telemetry-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  overflow: hidden;
}

.detail-row.full-width-row,
.telemetry-item.full-width-row {
  grid-column: 1 / -1;
  width: 100%;
  margin-top: 6px;
  padding-top: 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.detail-label,
.telemetry-label {
  font-size: 0.72rem;
  color: var(--text-dim);
  text-transform: uppercase;
  font-weight: 600;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.detail-value,
.telemetry-value {
  font-size: 0.92rem;
  color: var(--text-main);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.detail-value.mono,
.telemetry-value.mono {
  font-family: var(--font-mono);
  font-size: 0.82rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.event-feed-compact {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.compact-event-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background-color: rgba(255, 255, 255, 0.02);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  font-size: 0.86rem;
}

/* ==========================================================================
   VIEW 2: EVENT TIMELINE
   ========================================================================== */

.view-header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.section-desc {
  font-size: 0.84rem;
  color: var(--text-dim);
  margin-top: 4px;
}

.timeline-container {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.timeline-card {
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-md);
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: border-color 0.15s ease;
}

.timeline-card:hover {
  border-color: rgba(56, 189, 248, 0.4);
}

.timeline-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.timeline-badge-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.burst-badge {
  background-color: var(--accent-blue-bg);
  color: var(--accent-blue);
  border: 1px solid rgba(56, 189, 248, 0.3);
  font-size: 0.72rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
}

.entrypoint-badge {
  background-color: var(--accent-amber-bg);
  color: var(--accent-amber);
  border: 1px solid rgba(245, 158, 11, 0.3);
  font-size: 0.72rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.badge-ai-exec {
  background-color: rgba(168, 85, 247, 0.15);
  color: #c084fc;
  border: 1px solid rgba(168, 85, 247, 0.35);
  font-size: 0.72rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.badge-human-exec {
  background-color: rgba(56, 189, 248, 0.15);
  color: #38bdf8;
  border: 1px solid rgba(56, 189, 248, 0.35);
  font-size: 0.72rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.timeline-time {
  font-size: 0.78rem;
  color: var(--text-dim);
}

.timeline-summary {
  font-size: 0.92rem;
  color: var(--text-main);
  font-weight: 500;
}

.timeline-files-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.file-chip {
  background-color: var(--bg-input);
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  padding: 3px 8px;
  font-family: var(--font-mono);
  font-size: 0.74rem;
  color: var(--text-muted);
}

.file-chip.entrypoint {
  border-color: rgba(245, 158, 11, 0.4);
  color: #fbbf24;
}

.timeline-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.04);
}

.timeline-meta {
  font-size: 0.76rem;
  color: var(--text-dim);
}

/* ==========================================================================
   VIEW 3: PATCH DIFF VIEWER
   ========================================================================== */

.diff-viewer-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 16px;
  height: calc(100vh - var(--header-h) - 48px);
}

.diff-sidebar {
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-md);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
}

.diff-selector-header h3 {
  font-size: 0.95rem;
  font-weight: 600;
  color: #ffffff;
}

.diff-session-dropdown-container,
.diff-burst-dropdown-container,
.diff-event-dropdown-container {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.diff-session-dropdown-container select,
.diff-burst-dropdown-container select,
.diff-event-dropdown-container select {
  width: 100%;
}

.diff-files-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.76rem;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--text-dim);
}

.diff-files-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.diff-file-item {
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 0.78rem;
  cursor: pointer;
  text-align: left;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  transition: all 0.15s ease;
}

.diff-file-item .diff-file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
}

.diff-file-item .diff-file-stats {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.7rem;
  font-weight: 700;
  flex-shrink: 0;
}

.diff-file-item .stat-badge-add {
  color: var(--accent-green);
  background-color: var(--accent-green-bg);
  padding: 1px 5px;
  border-radius: 3px;
}

.diff-file-item .stat-badge-del {
  color: var(--accent-red);
  background-color: var(--accent-red-bg);
  padding: 1px 5px;
  border-radius: 3px;
}

.diff-file-item:hover {
  background-color: rgba(255, 255, 255, 0.04);
  color: var(--text-main);
}

.diff-file-item.active {
  background-color: var(--bg-input);
  border-color: var(--accent-blue);
  color: #ffffff;
  font-weight: 600;
}


.diff-viewer-main {
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.diff-code-header {
  padding: 12px 18px;
  border-bottom: 1px solid var(--border-card);
  display: flex;
  align-items: center;
  justify-content: space-between;
  background-color: rgba(0, 0, 0, 0.15);
}

.diff-code-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 0.88rem;
  font-weight: 600;
  color: #ffffff;
}

.diff-code-stats {
  display: flex;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  font-weight: 600;
}

.diff-stat-add {
  color: var(--accent-green);
  background-color: var(--accent-green-bg);
  padding: 2px 6px;
  border-radius: 4px;
}

.diff-stat-del {
  color: var(--accent-red);
  background-color: var(--accent-red-bg);
  padding: 2px 6px;
  border-radius: 4px;
}

.diff-code-container {
  flex: 1;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 0.82rem;
  background-color: var(--bg-terminal);
  padding: 12px 0;
  line-height: 1.6;
}

/* Diff Line Types */

.diff-line {
  display: flex;
  padding: 0 16px;
  white-space: pre-wrap;
  word-break: break-all;
}

.diff-line-num {
  width: 48px;
  min-width: 48px;
  color: var(--text-dim);
  user-select: none;
  text-align: right;
  padding-right: 14px;
  opacity: 0.6;
}

.diff-line-content {
  flex: 1;
}

.diff-add {
  background-color: rgba(34, 197, 94, 0.14);
  color: #4ade80;
  border-left: 3px solid #22c55e;
}

.diff-del {
  background-color: rgba(239, 68, 68, 0.14);
  color: #f87171;
  border-left: 3px solid #ef4444;
}

.diff-hunk {
  background-color: rgba(56, 189, 248, 0.12);
  color: #38bdf8;
  font-weight: 600;
  padding: 4px 16px;
  border-top: 1px solid rgba(56, 189, 248, 0.2);
  border-bottom: 1px solid rgba(56, 189, 248, 0.2);
}

.diff-file-header {
  background-color: rgba(255, 255, 255, 0.04);
  color: var(--text-muted);
  font-weight: 600;
  padding: 3px 16px;
}

/* ==========================================================================
   VIEW 4: WORKER LOGS
   ========================================================================== */

.logs-header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.logs-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.82rem;
  color: var(--text-muted);
  cursor: pointer;
}

.logs-terminal {
  background-color: var(--bg-terminal);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-md);
  padding: 16px 20px;
  height: calc(100vh - var(--header-h) - 140px);
  overflow-y: auto;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: #cbd5e1;
  line-height: 1.55;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.log-line {
  white-space: pre-wrap;
  word-break: break-all;
}

.log-line.info { color: #93c5fd; }
.log-line.warn { color: #fde047; }
.log-line.err  { color: #fca5a5; }
.log-line.json { color: #86efac; }

/* ==========================================================================
   MODAL: NEW SESSION LAUNCHER
   ========================================================================== */

.modal-backdrop {
  position: fixed;
  inset: 0;
  background-color: rgba(5, 8, 17, 0.75);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s ease;
}

.modal-backdrop.open {
  opacity: 1;
  pointer-events: auto;
}

.modal-box,
.modal-content,
#new-session-modal {
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-lg);
  width: 90%;
  max-width: 580px;
  max-height: 88vh;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
  transform: translateY(10px) scale(0.98);
  transition: transform 0.2s ease;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.modal-backdrop.open .modal-box {
  transform: translateY(0) scale(1);
}

.modal-header {
  padding: 14px 20px;
  border-bottom: 1px solid var(--border-card);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.modal-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.modal-title .icon {
  width: 20px;
  height: 20px;
}

.modal-title .icon.green { color: var(--accent-green); }

.modal-title h3 {
  font-size: 1.05rem;
  font-weight: 700;
  color: #ffffff;
}

.modal-close {
  background: transparent;
  border: none;
  color: var(--text-dim);
  font-size: 1.4rem;
  cursor: pointer;
  line-height: 1;
  padding: 4px;
}

.modal-close:hover {
  color: #ffffff;
}

.modal-body {
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;
  max-height: calc(88vh - 65px);
}

/* Custom dark scrollbar for modal */
.modal-body::-webkit-scrollbar,
.modal-content::-webkit-scrollbar,
.modal-box::-webkit-scrollbar {
  width: 6px;
}
.modal-body::-webkit-scrollbar-track,
.modal-content::-webkit-scrollbar-track,
.modal-box::-webkit-scrollbar-track {
  background: rgba(15, 23, 42, 0.6);
}
.modal-body::-webkit-scrollbar-thumb,
.modal-content::-webkit-scrollbar-thumb,
.modal-box::-webkit-scrollbar-thumb {
  background: rgba(148, 163, 184, 0.25);
  border-radius: 3px;
}
.modal-body::-webkit-scrollbar-thumb:hover,
.modal-content::-webkit-scrollbar-thumb:hover,
.modal-box::-webkit-scrollbar-thumb:hover {
  background: rgba(148, 163, 184, 0.45);
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-group label {
  font-size: 0.80rem;
  font-weight: 600;
  color: var(--text-main);
}

.required {
  color: var(--accent-red);
}

.form-control {
  background-color: var(--bg-input);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  color: #ffffff;
  font-size: 0.84rem;
  font-family: inherit;
  transition: border-color 0.15s ease;
}

.form-control.mono {
  font-family: var(--font-mono);
  font-size: 0.80rem;
}

.form-control:focus {
  outline: none;
  border-color: var(--accent-blue);
  box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.15);
}

.form-help {
  font-size: 0.71rem;
  color: var(--text-dim);
}

.form-help code {
  color: #38bdf8;
  font-family: var(--font-mono);
}

.form-check-group {
  padding: 2px 0;
}

.form-checkbox {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 0.80rem;
  color: var(--text-muted);
  cursor: pointer;
}

.form-checkbox input {
  margin-top: 2px;
}

/* Slider */

.slider-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.slider-val {
  font-family: var(--font-mono);
  font-size: 0.80rem;
  color: #38bdf8;
  font-weight: 600;
}

.badge-recommended {
  background-color: var(--accent-green-bg);
  color: var(--accent-green);
  border: 1px solid var(--accent-green-border);
  font-size: 0.65rem;
  padding: 1px 5px;
  border-radius: 4px;
  margin-left: 6px;
}

.form-range {
  width: 100%;
  accent-color: var(--accent-blue);
  cursor: pointer;
}

.slider-ticks {
  display: flex;
  justify-content: space-between;
  font-size: 0.66rem;
  color: var(--text-dim);
  margin-top: 2px;
}

/* Pre-flight Card */

.preflight-card {
  background-color: rgba(2, 132, 199, 0.08);
  border: 1px solid rgba(2, 132, 199, 0.25);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
}

.preflight-title {
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  color: #38bdf8;
  margin-bottom: 4px;
  letter-spacing: 0.05em;
}

.preflight-list {
  list-style: none;
  font-size: 0.72rem;
  color: #94a3b8;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.modal-footer {
  padding-top: 6px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-shrink: 0;
}

/* ==========================================================================
   BUTTONS & FORMS
   ========================================================================== */

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 0.84rem;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: inherit;
}

.btn-primary {
  background: linear-gradient(135deg, #10b981, #059669);
  color: #ffffff;
  box-shadow: 0 2px 8px rgba(16, 185, 129, 0.25);
}

.btn-primary:hover {
  background: linear-gradient(135deg, #059669, #047857);
}

.btn-secondary {
  background-color: var(--bg-card);
  border-color: var(--border-card);
  color: var(--text-main);
}

.btn-secondary:hover {
  background-color: var(--border-card);
}

.btn-danger {
  background-color: var(--accent-red-bg);
  border-color: rgba(239, 68, 68, 0.35);
  color: var(--accent-red);
}

.btn-danger:hover {
  background-color: #ef4444;
  color: #ffffff;
}

.btn-link {
  background: transparent;
  border: none;
  color: #38bdf8;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
}

.btn-link:hover {
  text-decoration: underline;
}

.btn-sm {
  padding: 5px 12px;
  font-size: 0.78rem;
}

.btn-block {
  width: 100%;
}

.btn-icon {
  background: transparent;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
}

.btn-icon:hover {
  color: var(--text-main);
  background-color: rgba(255, 255, 255, 0.05);
}

.icon { width: 16px; height: 16px; }
.icon-sm { width: 14px; height: 14px; }

.form-select {
  background-color: var(--bg-input);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-main);
  padding: 8px 10px;
  font-size: 0.82rem;
  font-family: inherit;
  width: 100%;
}

.form-select:focus {
  outline: none;
  border-color: var(--accent-blue);
}

/* Toast Notifications */

.toast-container {
  position: fixed;
  bottom: 24px;
  right: 24px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 300;
  transition: bottom 0.25s ease;
}

/* Offset toasts when global progress tray is active */
.global-job-tray:not(.hidden) ~ .toast-container {
  bottom: 170px;
}

.toast {
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  color: #ffffff;
  padding: 10px 16px;
  border-radius: var(--radius-sm);
  font-size: 0.84rem;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 280px;
  max-width: 420px;
  animation: slideIn 0.2s ease-out;
}

.toast.success { border-left: 4px solid var(--accent-green); }
.toast.error   { border-left: 4px solid var(--accent-red); }
.toast.info    { border-left: 4px solid var(--accent-blue); }
.toast.warning { border-left: 4px solid #f59e0b; }

.toast-icon { font-size: 1.05rem; flex-shrink: 0; }
.toast-body { display: flex; flex-direction: column; gap: 2px; flex: 1; }
.toast-title { font-weight: 600; font-size: 0.82rem; color: #ffffff; }
.toast-msg { font-size: 0.79rem; color: var(--text-dim); word-break: break-word; }
.toast-close {
  background: transparent;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  font-size: 1rem;
  padding: 0 4px;
  line-height: 1;
}
.toast-close:hover { color: #ffffff; }

/* Floating Global Job & Rate-Limit Progress Tray */
.global-job-tray {
  position: fixed;
  bottom: 24px;
  right: 24px;
  width: 340px;
  background: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-md);
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(99, 102, 241, 0.15);
  z-index: 250;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: slideInUp 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  backdrop-filter: blur(8px);
}

@keyframes slideInUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

.tray-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: rgba(255, 255, 255, 0.03);
  border-bottom: 1px solid var(--border-card);
}

.tray-title-area {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tray-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(99, 102, 241, 0.3);
  border-top-color: var(--accent-purple);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  display: inline-block;
}

.tray-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text-bright);
}

.tray-close-btn {
  background: transparent;
  border: none;
  color: var(--text-dim);
  font-size: 1.1rem;
  line-height: 1;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: color 0.15s, background 0.15s;
}

.tray-close-btn:hover {
  color: var(--text-bright);
  background: rgba(255, 255, 255, 0.08);
}

.tray-body {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tray-project {
  font-size: 0.78rem;
  color: var(--text-dim);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tray-progress-wrap {
  width: 100%;
  height: 6px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 3px;
  overflow: hidden;
}

.tray-progress-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-blue), var(--accent-purple));
  border-radius: 3px;
  transition: width 0.3s ease;
}

.tray-stats-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.75rem;
  color: var(--text-dim);
}

.tray-percent {
  font-weight: 600;
  color: var(--text-bright);
}

.tray-cooldown-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.3);
  color: #fbbf24;
  padding: 5px 8px;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  font-weight: 500;
  margin-top: 2px;
}

.tray-cooldown-badge .cooldown-icon {
  font-size: 0.85rem;
}

@keyframes slideIn {
  from { opacity: 0; transform: translateX(20px); }
  to { opacity: 1; transform: translateX(0); }
}

.hidden { display: none !important; }

.empty-state {
  text-align: center;
  padding: 40px 20px;
  color: var(--text-dim);
  font-size: 0.88rem;
}

/* ==========================================================================
   PHASE 4: SELECTIVE POWERS, TERMINAL AUDIT & FILE READ TRACKER
   ========================================================================== */

/* Powers Ribbon */
.powers-ribbon {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  margin-right: 16px;
}

.power-pill {
  font-size: 0.75rem;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: var(--radius-full);
  background-color: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border-subtle);
  color: var(--text-dim);
  transition: all 0.15s ease;
  user-select: none;
}

.power-pill.active {
  background-color: rgba(56, 189, 248, 0.12);
  border-color: rgba(56, 189, 248, 0.35);
  color: #38bdf8;
}

.power-pill.disabled {
  opacity: 0.4;
  text-decoration: line-through;
}

/* Modal Features Checklist */
.features-checklist {
  display: flex;
  flex-direction: column;
  gap: 5px;
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  margin-top: 4px;
}

.features-checklist .form-checkbox {
  margin-bottom: 0;
  font-size: 0.78rem;
}

/* Timeline Analyzed Files Panel */
.timeline-reads-panel {
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-md);
  margin-top: 24px;
  overflow: hidden;
}

.reads-panel-header {
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-card);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.reads-panel-header h3 {
  font-size: 0.92rem;
  font-weight: 600;
  color: #ffffff;
  display: flex;
  align-items: center;
  gap: 8px;
}

.reads-list {
  padding: 14px 18px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-height: 220px;
  overflow-y: auto;
}

.read-chip {
  background-color: var(--bg-main);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 4px 10px;
  font-size: 0.78rem;
  font-family: var(--font-mono);
  color: var(--text-main);
  display: flex;
  align-items: center;
  gap: 6px;
}

.read-chip .proc-tag {
  color: var(--text-dim);
  font-size: 0.72rem;
}

/* Terminal View */
.terminal-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.exec-card {
  background-color: var(--bg-card);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-md);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: border-color 0.15s ease;
}

.exec-card:hover {
  border-color: rgba(56, 189, 248, 0.3);
}

.exec-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.exec-meta-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.exec-badge {
  font-size: 0.72rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.exec-badge.success {
  background-color: var(--accent-green-bg);
  color: var(--accent-green);
  border: 1px solid rgba(16, 185, 129, 0.3);
}

.exec-badge.failed {
  background-color: var(--accent-red-bg);
  color: var(--accent-red);
  border: 1px solid rgba(239, 68, 68, 0.3);
}

.exec-cmd-box {
  background-color: var(--bg-main);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  font-family: var(--font-mono);
  font-size: 0.85rem;
  color: #38bdf8;
  word-break: break-all;
}

.exec-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.76rem;
  color: var(--text-dim);
}

/* ==========================================================================
   PHASE 5: ON-DEMAND AI INTELLIGENCE & GITHUB SYNC
   ========================================================================== */

/* AI Analysis Button */
.btn-ai {
  background: linear-gradient(135deg, rgba(168, 85, 247, 0.18), rgba(56, 189, 248, 0.18));
  border: 1px solid rgba(168, 85, 247, 0.45);
  color: #c084fc;
  font-weight: 600;
  transition: all 0.18s ease;
  position: relative;
}

.btn-ai:hover:not(:disabled) {
  background: linear-gradient(135deg, rgba(168, 85, 247, 0.35), rgba(56, 189, 248, 0.35));
  border-color: rgba(168, 85, 247, 0.7);
  color: #ffffff;
  box-shadow: 0 0 14px rgba(168, 85, 247, 0.35);
  transform: translateY(-1px);
}

.btn-ai:disabled {
  opacity: 0.65;
  cursor: wait;
}

.ai-sparkle {
  font-size: 0.85rem;
  line-height: 1;
}

/* AI Intent & Functional Gains Card */
.ai-analysis-card {
  margin-top: 12px;
  background: linear-gradient(135deg, rgba(24, 21, 58, 0.75), rgba(15, 23, 42, 0.85));
  border: 1px solid rgba(168, 85, 247, 0.35);
  border-left: 3px solid #a855f7;
  border-radius: var(--radius-sm);
  padding: 14px 16px;
  animation: fadeIn 0.25s ease-out;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
}

.ai-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(168, 85, 247, 0.2);
}

.ai-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.76rem;
  font-weight: 700;
  color: #c084fc;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.ai-provider-badge {
  font-size: 0.68rem;
  font-family: var(--font-mono);
  color: var(--text-dim);
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  padding: 2px 8px;
  border-radius: var(--radius-full);
}

.ai-section {
  margin-bottom: 10px;
}

.ai-section:last-child {
  margin-bottom: 0;
}

.ai-section-title {
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  color: #38bdf8;
  margin-bottom: 3px;
  letter-spacing: 0.04em;
}

.ai-intent-text {
  font-size: 0.84rem;
  color: #f1f5f9;
  line-height: 1.45;
}

.ai-gains-text {
  font-size: 0.84rem;
  color: #34d399;
  line-height: 1.45;
}

.ai-summary-list {
  margin: 4px 0 0 16px;
  padding: 0;
  font-size: 0.80rem;
  color: var(--text-muted);
  line-height: 1.4;
}

.ai-summary-list li {
  margin-bottom: 2px;
}

/* Push to GitHub Header Button */
#btn-header-push-git {
  transition: all 0.15s ease;
}

#btn-header-push-git:hover {
  background-color: rgba(255, 255, 255, 0.12);
  border-color: rgba(255, 255, 255, 0.3);
  color: #ffffff;
}

/* GitHub Settings & Milestone Sync Review Modals */
#btn-header-git-settings {
  transition: all 0.15s ease;
}

#btn-header-git-settings:hover {
  background-color: rgba(56, 189, 248, 0.15);
  border-color: rgba(56, 189, 248, 0.4);
  color: #38bdf8;
}

.git-test-badge {
  font-family: var(--font-mono);
  font-size: 0.76rem;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  gap: 6px;
  word-break: break-all;
}

.git-test-badge.success {
  background-color: var(--accent-green-bg);
  border: 1px solid var(--accent-green-border);
  color: var(--accent-green);
}

.git-test-badge.error {
  background-color: var(--accent-red-bg);
  border: 1px solid rgba(239, 68, 68, 0.35);
  color: #fca5a5;
}

.git-test-badge.testing {
  background-color: var(--accent-blue-bg);
  border: 1px solid rgba(56, 189, 248, 0.35);
  color: #38bdf8;
}

#sync-review-modal textarea {
  resize: vertical;
  min-height: 70px;
}

/* ==========================================================================
   SIDEBAR COLLAPSIBLE ACCORDIONS
   ========================================================================== */
.accordion-section {
  display: flex;
  flex-direction: column;
  margin-bottom: 8px;
  border-radius: var(--radius-sm);
  background-color: rgba(255, 255, 255, 0.015);
  border: 1px solid rgba(255, 255, 255, 0.06);
  overflow: hidden;
  transition: all 0.2s ease;
}

.accordion-section.open {
  background-color: rgba(255, 255, 255, 0.03);
  border-color: rgba(255, 255, 255, 0.1);
}

.accordion-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 8px 10px;
  background: transparent;
  border: none;
  cursor: pointer;
  color: #f1f5f9;
  text-align: left;
  transition: background-color 0.15s ease;
}

.accordion-header:hover {
  background-color: rgba(255, 255, 255, 0.05);
}

.accordion-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #f8fafc;
}

.accordion-chevron {
  display: inline-block;
  font-size: 0.72rem;
  color: var(--text-dim);
  transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.accordion-section.open .accordion-chevron {
  transform: rotate(90deg);
  color: #38bdf8;
}

.accordion-body {
  display: none;
  padding: 4px 8px 8px 8px;
}

.accordion-section.open .accordion-body {
  display: block;
}

/* Current Session Telemetry Full Width Row */
.session-details-grid .full-width-row {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}

.session-details-grid .powers-ribbon {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

/* ==========================================================================
   DEBOUNCE BURST MONITOR CARD
   ========================================================================== */
.debounce-monitor-card {
  margin-bottom: 16px;
  background: linear-gradient(180deg, rgba(30, 41, 59, 0.7) 0%, rgba(15, 23, 42, 0.8) 100%);
  border: 1px solid rgba(56, 189, 248, 0.2);
}

.monitor-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.debounce-pulse-indicator {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: all 0.3s ease;
}

.debounce-pulse-indicator.idle {
  background-color: #10b981;
  box-shadow: 0 0 6px rgba(16, 185, 129, 0.6);
}

.debounce-pulse-indicator.active {
  background-color: #f59e0b;
  box-shadow: 0 0 10px #f59e0b;
  animation: pulse-ring 1s infinite;
}

.debounce-pulse-indicator.stopped {
  background-color: #64748b;
  box-shadow: none;
}

@keyframes pulse-ring {
  0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.7); }
  70% { transform: scale(1.15); box-shadow: 0 0 0 6px rgba(245, 158, 11, 0); }
  100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }
}

.debounce-status-badge {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 9999px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.debounce-status-badge.idle {
  background-color: rgba(16, 185, 129, 0.15);
  color: #34d399;
  border: 1px solid rgba(16, 185, 129, 0.3);
}

.debounce-status-badge.active {
  background-color: rgba(245, 158, 11, 0.15);
  color: #fbbf24;
  border: 1px solid rgba(245, 158, 11, 0.4);
  animation: pulse 1.2s infinite;
}

.debounce-status-badge.stopped {
  background-color: rgba(100, 116, 139, 0.15);
  color: #94a3b8;
  border: 1px solid rgba(100, 116, 139, 0.3);
}

.debounce-progress-container {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.debounce-progress-bar {
  width: 100%;
  height: 8px;
  background-color: rgba(15, 23, 42, 0.8);
  border-radius: 9999px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.debounce-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #38bdf8, #818cf8, #a855f7);
  background-size: 200% 100%;
  border-radius: 9999px;
  transition: width 0.25s linear;
}

.debounce-progress-fill.active {
  animation: progressStripes 1.5s linear infinite;
}

@keyframes progressStripes {
  0% { background-position: 100% 0; }
  100% { background-position: 0 0; }
}

.debounce-progress-labels {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.78rem;
  color: var(--text-muted);
}

/* ==========================================================================
   GITHUB PUSH 3-STEP PROGRESS STEPPER
   ========================================================================== */
.sync-stepper-container {
  margin-top: 10px;
  margin-bottom: 12px;
  padding: 12px 14px;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(168, 85, 247, 0.3);
  border-radius: var(--radius-sm);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.stepper-progress-bar {
  width: 100%;
  height: 6px;
  background-color: rgba(255, 255, 255, 0.08);
  border-radius: 9999px;
  overflow: hidden;
}

.stepper-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #a855f7, #38bdf8);
  transition: width 0.35s ease;
}

.stepper-steps {
  display: flex;
  justify-content: space-between;
  position: relative;
}

.stepper-step {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.75rem;
  color: var(--text-dim);
}

.stepper-step .step-num {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background-color: rgba(255, 255, 255, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.68rem;
  font-weight: 700;
  color: var(--text-dim);
}

.stepper-step.active {
  color: #c084fc;
  font-weight: 600;
}

.stepper-step.active .step-num {
  background-color: #a855f7;
  color: #ffffff;
  box-shadow: 0 0 8px rgba(168, 85, 247, 0.6);
}

.stepper-step.done {
  color: #34d399;
}

.stepper-step.done .step-num {
  background-color: #10b981;
  color: #ffffff;
}

.stepper-status-msg {
  font-size: 0.74rem;
  color: #cbd5e1;
  font-style: italic;
  text-align: center;
}

/* ==========================================================================
   AI ANALYSIS PROGRESS INDICATOR
   ========================================================================== */
.ai-progress-track {
  width: 100%;
  height: 6px;
  background-color: rgba(168, 85, 247, 0.15);
  border-radius: 9999px;
  overflow: hidden;
  margin: 8px 0 6px 0;
  position: relative;
}

.ai-progress-fill {
  width: 40%;
  height: 100%;
  background: linear-gradient(90deg, #a855f7, #ec4899, #38bdf8);
  border-radius: 9999px;
  animation: aiProgressPulse 1.4s ease-in-out infinite alternate;
}

@keyframes aiProgressPulse {
  0% { transform: translateX(0); width: 30%; }
  100% { transform: translateX(200%); width: 45%; }
}

.ai-progress-status {
  font-size: 0.76rem;
  color: #c084fc;
  font-style: italic;
}

/* ==========================================================================
   AI SETTINGS MODAL & PROVIDER CARDS
   ========================================================================== */
#btn-header-ai-settings {
  transition: all 0.15s ease;
}

#btn-header-ai-settings:hover {
  background-color: rgba(168, 85, 247, 0.15);
  border-color: rgba(168, 85, 247, 0.4);
  color: #c084fc;
}

.provider-config-box {
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  padding: 12px 14px;
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.provider-box-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 2px;
}

.provider-box-title {
  font-size: 0.78rem;
  font-weight: 700;
  color: #c084fc;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.test-status-badge {
  font-family: var(--font-mono);
  font-size: 0.76rem;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  gap: 6px;
  word-break: break-all;
  margin-top: 4px;
}

.test-status-badge.success {
  background-color: var(--accent-green-bg);
  border: 1px solid var(--accent-green-border);
  color: var(--accent-green);
}

.test-status-badge.error {
  background-color: var(--accent-red-bg);
  border: 1px solid rgba(239, 68, 68, 0.35);
  color: #fca5a5;
}

.test-status-badge.testing {
  background-color: var(--accent-blue-bg);
  border: 1px solid rgba(56, 189, 248, 0.35);
  color: #38bdf8;
}

.heuristic-notice-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px;
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: var(--radius-sm);
  color: #fef3c7;
}

.heuristic-icon {
  font-size: 1.3rem;
  line-height: 1;
}

/* ==========================================================================
   RESUME BUTTON, LIVE ACTION TICKER & TIMELINE FILTERS
   ========================================================================== */

.btn-resume {
  background-color: #16a34a !important;
  color: #ffffff !important;
  border-color: #22c55e !important;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  box-shadow: 0 0 10px rgba(34, 197, 94, 0.25);
  transition: all 0.15s ease-in-out;
}

.btn-resume:hover {
  background-color: #15803d !important;
  box-shadow: 0 0 14px rgba(34, 197, 94, 0.45);
}

/* Live Activity Stream Ticker Card */
.live-ticker-card {
  border: 1px solid rgba(59, 130, 246, 0.25);
  background: linear-gradient(180deg, rgba(30, 41, 59, 0.5) 0%, rgba(15, 23, 42, 0.7) 100%);
  margin-bottom: 20px;
}

.live-dot-pulse {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background-color: #3b82f6;
  box-shadow: 0 0 8px #3b82f6;
  display: inline-block;
  animation: pulse-ticker 1.6s infinite;
}

@keyframes pulse-ticker {
  0% { transform: scale(0.9); opacity: 0.7; }
  50% { transform: scale(1.3); opacity: 1; box-shadow: 0 0 12px #60a5fa; }
  100% { transform: scale(0.9); opacity: 0.7; }
}

.live-ticker-feed {
  min-height: 220px;
  max-height: 320px;
  overflow-y: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 0.82rem;
  line-height: 1.6;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background-color: rgba(10, 15, 28, 0.85);
  border-radius: 8px;
  border: 1px solid rgba(59, 130, 246, 0.2);
}

.ticker-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 3px 6px;
  border-radius: 4px;
  transition: background-color 0.1s ease;
}

.ticker-row:hover {
  background-color: rgba(255, 255, 255, 0.05);
}

.ticker-ts {
  color: var(--text-dim);
  font-size: 0.72rem;
  flex-shrink: 0;
}

.ticker-badge {
  font-size: 0.68rem;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 3px;
  text-transform: uppercase;
  flex-shrink: 0;
}

.ticker-badge.write { background-color: rgba(16, 185, 129, 0.18); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.4); }
.ticker-badge.read  { background-color: rgba(14, 165, 233, 0.18); color: #0ea5e9; border: 1px solid rgba(14, 165, 233, 0.4); }
.ticker-badge.exec  { background-color: rgba(168, 85, 247, 0.18); color: #a855f7; border: 1px solid rgba(168, 85, 247, 0.4); }
.ticker-badge.burst { background-color: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.4); }

.ticker-file {
  color: var(--text-main);
  word-break: break-all;
}

.ticker-details {
  color: var(--text-muted);
  font-size: 0.74rem;
  margin-left: auto;
  flex-shrink: 0;
}

.ticker-empty {
  color: var(--text-dim);
  font-style: italic;
  padding: 12px;
  text-align: center;
}

/* Timeline Filter Group */
.filter-btn-group {
  display: inline-flex;
  gap: 4px;
  background-color: var(--bg-surface-2);
  padding: 3px;
  border-radius: 6px;
  border: 1px solid var(--border-subtle);
}

.filter-pill {
  border: none;
  background: transparent;
  color: var(--text-muted);
  font-size: 0.78rem;
  font-weight: 500;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.12s ease;
}

.filter-pill:hover {
  color: var(--text-main);
  background-color: rgba(255, 255, 255, 0.05);
}

.filter-pill.active {
  background-color: var(--accent-blue);
  color: #ffffff;
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
}

/* Key Vault Selector Row */
.key-vault-selector-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.key-vault-selector-row .form-select {
  flex: 1;
}

.btn-icon-danger {
  padding: 6px 10px;
  background-color: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.35);
  color: #f87171;
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}

.btn-icon-danger:hover {
  background-color: rgba(239, 68, 68, 0.28);
  border-color: #ef4444;
  color: #ffffff;
}

/* ==========================================================================
   PROJECT CONFIGURATION & SPECS CARD
   ========================================================================== */

.project-specs-card {
  border: 1px solid rgba(56, 189, 248, 0.25);
  background: linear-gradient(180deg, rgba(30, 41, 59, 0.45) 0%, rgba(15, 23, 42, 0.65) 100%);
  margin-bottom: 20px;
}

.specs-header-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.specs-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px 24px;
}

.spec-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.spec-label {
  font-size: 0.72rem;
  color: var(--text-dim);
  text-transform: uppercase;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.spec-value {
  font-size: 0.88rem;
  color: var(--text-main);
  font-weight: 500;
}

.spec-value.mono {
  font-family: var(--font-mono);
  font-size: 0.80rem;
  word-break: break-all;
}

.specs-checklist {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 2px;
}

.spec-check-pill {
  font-size: 0.70rem;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.05);
  color: var(--text-dim);
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.spec-check-pill.active {
  background: rgba(34, 197, 94, 0.15);
  color: #4ade80;
  border-color: rgba(34, 197, 94, 0.35);
}

.spec-remote-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-xs {
  padding: 3px 8px;
  font-size: 0.72rem;
  border-radius: 4px;
}

.icon-xs {
  width: 12px;
  height: 12px;
}

/* ==========================================================================
   HIERARCHICAL PATCH DIFF VIEWER (3-Step Navigation)
   ========================================================================== */

.diff-step-card {
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  padding: 12px 14px;
  margin-bottom: 12px;
}

.diff-step-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.diff-step-header h4 {
  font-size: 0.82rem;
  font-weight: 700;
  color: #e2e8f0;
  letter-spacing: 0.02em;
  margin: 0;
}

.diff-step-num {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #38bdf8;
  color: #0f172a;
  font-size: 0.72rem;
  font-weight: 800;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-right: 6px;
  flex-shrink: 0;
}

.step-3-header-card {
  margin-bottom: 10px;
  padding: 10px 14px;
}

.diff-ai-banner {
  margin-bottom: 12px;
}

.diff-ai-banner:empty {
  display: none;
}

.diff-ai-banner-empty {
  background: rgba(30, 41, 59, 0.6);
  border: 1px dashed rgba(255, 255, 255, 0.15);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--text-dim);
  font-size: 0.80rem;
}

.diff-ai-banner-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn-diff-analyze {
  white-space: nowrap;
}

/* ==========================================================================
   BATCH AI PROGRESS, PROJECT DELETION & TRASH BIN STYLES
   ========================================================================== */

/* Button danger outline */
.btn-danger-outline {
  background: transparent;
  border: 1px solid rgba(239, 68, 68, 0.4);
  color: #f87171;
  transition: all 0.15s ease;
}

.btn-danger-outline:hover {
  background: rgba(239, 68, 68, 0.15);
  border-color: #ef4444;
  color: #ffffff;
}

.btn-xs {
  font-size: 0.72rem;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}

/* Batch Progress Box */
.batch-progress-box {
  background: linear-gradient(135deg, rgba(88, 28, 135, 0.25), rgba(30, 27, 75, 0.35));
  border: 1px solid rgba(168, 85, 247, 0.35);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
  margin-bottom: 16px;
  box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
}

.batch-progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.batch-progress-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.batch-pulse-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(168, 85, 247, 0.3);
  border-top-color: #c084fc;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  display: inline-block;
}

.batch-title {
  font-size: 0.86rem;
  color: #e9d5ff;
}

.batch-progress-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.batch-pct {
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 0.84rem;
  color: #c084fc;
}

.batch-progress-track {
  width: 100%;
  height: 6px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 6px;
}

.batch-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #a855f7, #38bdf8);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.batch-progress-labels {
  display: flex;
  justify-content: space-between;
  font-size: 0.76rem;
  color: var(--text-dim);
}

/* Delete Project Modal Scopes */
.delete-scope-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 8px;
}

.scope-option {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s ease;
}

.scope-option:hover {
  background: rgba(30, 41, 59, 0.6);
  border-color: rgba(255, 255, 255, 0.2);
}

.scope-option input[type="radio"] {
  margin-top: 3px;
  accent-color: #a855f7;
  cursor: pointer;
}

.scope-option.selected {
  border-color: rgba(168, 85, 247, 0.6);
  background: rgba(88, 28, 135, 0.15);
}

.scope-option.danger-scope.selected {
  border-color: rgba(239, 68, 68, 0.6);
  background: rgba(127, 29, 29, 0.15);
}

.scope-info {
  flex: 1;
}

.scope-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.scope-badge {
  font-size: 0.70rem;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: rgba(168, 85, 247, 0.15);
  color: #c084fc;
  border: 1px solid rgba(168, 85, 247, 0.3);
}

.badge-danger {
  background: rgba(239, 68, 68, 0.15);
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.3);
}

.scope-desc {
  font-size: 0.80rem;
  color: var(--text-dim);
  margin: 0;
  line-height: 1.4;
}

/* Selective Bursts Checklist Box */
.selective-bursts-box {
  background: rgba(10, 15, 30, 0.7);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  margin-left: 28px;
  margin-top: -4px;
  margin-bottom: 8px;
}

.selective-bursts-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.80rem;
  color: var(--text-dim);
  margin-bottom: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.selective-bursts-list {
  max-height: 160px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.burst-check-item {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.78rem;
  padding: 4px 6px;
  border-radius: 4px;
}

.burst-check-item:hover {
  background: rgba(255, 255, 255, 0.05);
}

.burst-check-item input[type="checkbox"] {
  accent-color: #ef4444;
}

/* Remote Deletion Option Box */
.delete-remote-box {
  background: rgba(127, 29, 29, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  margin-left: 28px;
  margin-top: -4px;
  margin-bottom: 8px;
}

/* Trash Bin Modal Items */
.trash-items-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 480px;
  overflow-y: auto;
}

.trash-item-card {
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  transition: all 0.15s ease;
}

.trash-item-card:hover {
  background: rgba(30, 41, 59, 0.5);
  border-color: rgba(255, 255, 255, 0.15);
}

.trash-item-main {
  flex: 1;
}

.trash-item-top {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
}

.trash-item-title {
  font-weight: 700;
  font-size: 0.90rem;
  color: #f1f5f9;
}

.trash-scope-badge {
  font-size: 0.70rem;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: rgba(245, 158, 11, 0.15);
  color: #fbbf24;
  border: 1px solid rgba(245, 158, 11, 0.3);
  text-transform: capitalize;
}

.trash-item-meta {
  font-size: 0.78rem;
  color: var(--text-dim);
  display: flex;
  align-items: center;
  gap: 12px;
}

.trash-item-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

/* ==========================================================================
   COMMAND-LEVEL AI EXPLANATION STYLING
   ========================================================================== */

.exec-ai-card {
  border-left: 3px solid #c084fc !important;
  background: rgba(168, 85, 247, 0.04) !important;
  margin-top: 8px;
}

.exec-ai-card .ai-pill {
  background: rgba(168, 85, 247, 0.15) !important;
  color: #c084fc !important;
  border-color: rgba(168, 85, 247, 0.3) !important;
}

.timeline-card.exec-event-card .timeline-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}

.exec-card .exec-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}

/* ==========================================================================
   SPLIT-SCREEN & RESPONSIVE ADAPTIVE HEADER
   ========================================================================== */

@media (max-width: 1050px) {
  .header-left {
    min-width: 140px;
    max-width: 220px;
  }
  .header-target-path {
    max-width: 180px;
  }
  .path-val {
    max-width: 130px;
  }
  .telemetry-pill {
    display: none !important;
  }
  .telemetry-compact {
    display: flex !important;
    align-items: center;
    background-color: var(--bg-card);
    border: 1px solid var(--border-card);
    border-radius: var(--radius-sm);
    padding: 5px 10px;
    font-size: 0.78rem;
    font-weight: 600;
    color: #ffffff;
    white-space: nowrap;
  }
  .header-right {
    gap: 6px;
  }
  .header-right .btn-sm {
    padding: 5px 8px;
    font-size: 0.78rem;
  }
}

@media (max-width: 780px) {
  .sidebar {
    width: 64px !important;
    min-width: 64px !important;
  }
  .sidebar .brand-text,
  .sidebar .sidebar-action,
  .sidebar .nav-section-title,
  .sidebar .sidebar-projects,
  .sidebar .nav-label,
  .sidebar .badge {
    display: none !important;
  }
  .sidebar .sidebar-brand {
    padding: 16px 8px;
    justify-content: center;
    position: relative;
  }
  .sidebar .brand-icon {
    width: 32px;
    height: 32px;
  }
  .sidebar .sidebar-toggle-btn {
    display: none;
  }
  .sidebar .sidebar-nav {
    padding: 12px 6px;
    gap: 6px;
  }
  .sidebar .nav-item {
    justify-content: center;
    padding: 10px 0;
  }
  .sidebar .nav-item .icon {
    margin: 0;
  }
  .header-right .btn .btn-text {
    display: none !important;
  }
  .header-right .btn {
    padding: 6px 8px;
  }
  .top-header, .header-bar {
    padding: 8px 12px;
    gap: 8px;
  }
}

/* Debounce Quick Presets Row */
.slider-presets-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 6px;
}

.slider-presets-row .btn-debounce-preset {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 3px 8px;
  font-size: 0.72rem;
  cursor: pointer;
  transition: all 0.15s ease;
}

.slider-presets-row .btn-debounce-preset:hover,
.slider-presets-row .btn-debounce-preset.active {
  background: rgba(56, 189, 248, 0.15);
  border-color: var(--accent-blue);
  color: #ffffff;
}

/* Interrupted Session Banner */
.interrupted-alert-banner {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.35);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  color: #fca5a5;
  font-size: 0.82rem;
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.badge-interrupted {
  background: rgba(239, 68, 68, 0.18);
  color: #f87171;
  border: 1px solid rgba(239, 68, 68, 0.35);
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.72rem;
}

.btn-rollback-burst {
  border-color: rgba(239, 68, 68, 0.4);
  color: #f87171;
  transition: all 0.15s ease;
}

.btn-rollback-burst:hover {
  background: rgba(239, 68, 68, 0.15);
  border-color: #ef4444;
  color: #ffffff;
}
```

---

