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
