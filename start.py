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
