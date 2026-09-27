"""
scripts/shell/cmd_parser.py
===========================
Token scrubbing and command-line workspace file inspection utilities.
"""

from __future__ import annotations

import re
import shlex
from datetime import datetime, timezone
from pathlib import Path


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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_path_inside_target(path_str: str, target_path: Path) -> bool:
    """Check if *path_str* is inside *target_path*."""
    try:
        p = Path(path_str).resolve()
        p.relative_to(target_path)
        return True
    except (ValueError, OSError):
        return False


def _detect_file_reads_from_cmd(cmd_str: str, target_path: Path) -> list[str]:
    """Parse command line strings for referenced workspace files and emit READ events."""
    if not cmd_str or not target_path.exists():
        return []

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
                if _is_path_inside_target(str(p), target_path):
                    resolved = p.resolve()
            else:
                cand_path = (target_path / p).resolve()
                if cand_path.exists() and cand_path.is_file():
                    resolved = cand_path

            if resolved and resolved.exists() and resolved.is_file():
                rel_p = str(resolved.relative_to(target_path)).replace("\\", "/")
                try:
                    from scripts.watcher_proc import IGNORED_DIRS, IGNORED_EXTENSIONS
                except (ImportError, ModuleNotFoundError):
                    try:
                        from spd_analysis_engine.scripts.watcher_proc import IGNORED_DIRS, IGNORED_EXTENSIONS
                    except (ImportError, ModuleNotFoundError):
                        IGNORED_DIRS = {".git", ".spd", "node_modules", "__pycache__", ".venv"}
                        IGNORED_EXTENSIONS = {".pyc", ".tmp", ".log"}
                parts = set(resolved.parts)
                if not parts.intersection(IGNORED_DIRS) and resolved.suffix.lower() not in IGNORED_EXTENSIONS:
                    detected_files.append(rel_p)
        except Exception:
            continue

    return list(dict.fromkeys(detected_files))
