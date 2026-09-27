"""
scripts/shell/classifier.py
===========================
Command filtering, ignored binary classifications, and AI vs human dev detection.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import psutil

try:
    from ..ide_profiles import is_process_whitelisted, get_profile, IDE_PROFILES
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ide_profiles import is_process_whitelisted, get_profile, IDE_PROFILES
    except (ImportError, ModuleNotFoundError):
        try:
            from scripts.ide_profiles import is_process_whitelisted, get_profile, IDE_PROFILES
        except (ImportError, ModuleNotFoundError):
            def is_process_whitelisted(proc: Any, profile_name: str = "antigravity", custom_marker: str | None = None) -> bool:
                return True
            IDE_PROFILES = {}

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
