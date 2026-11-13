"""
bench/audit_search.py
Audit search tool for dangerous patterns and invariant verifications.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

dangerous_patterns = [
    ("threading.Timer", r"threading\.Timer"),
    ("--allow-empty", r"--allow-empty"),
    ("git add <huge list>", r"git.*add.*(\*|--all)"),
    ("psutil.Process(...).terminate()", r"\.terminate\(\)"),
    ("process_iter(... substring ...)", r"process_iter"),
    ("consolidate_project_history(", r"consolidate_project_history\("),
    ("auto_synthesize=True", r"auto_synthesize\s*=\s*True"),
    ("wal_checkpoint", r"wal_checkpoint"),
    ('rglob("*")', r"rglob\([\"']\*[\"']\)"),
    ("*.lock cleanup", r"\.lock.*unlink|unlink.*\.lock"),
    ("Access-Control-Allow-Origin: *", r"Access-Control-Allow-Origin.*\*"),
    ("api key in URL", r"(https?://[^\s\"']+[?&](key|api_key)=|key=[A-Za-z0-9_\-]{15,})"),
]

for label, pat in dangerous_patterns:
    print(f"\n========================================================")
    print(f"=== SEARCH: {label} ===")
    print(f"========================================================")
    regex = re.compile(pat, re.IGNORECASE)
    matches = []
    for p in Path(".").rglob("*"):
        if not p.is_file():
            continue
        if any(part in p.parts for part in (".venvconda", ".git", "node_modules", "__pycache__")):
            continue
        if "codebase_bundle" in p.name:
            continue
        if p.suffix not in (".py", ".js", ".html", ".md", ".sh", ".ps1"):
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            for idx, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append((str(p), idx, line.strip()))
        except Exception:
            pass
    for m in matches[:25]:
        print(f"  {m[0]}:{m[1]}: {m[2]}")
    if len(matches) > 25:
        print(f"  ... and {len(matches) - 25} more matches (total {len(matches)})")
    if not matches:
        print("  NONE FOUND!")
