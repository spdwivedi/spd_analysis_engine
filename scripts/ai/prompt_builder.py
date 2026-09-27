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

    dir_tree_block = f"\nProject Directory Structure:\n```\n{dir_tree}\n```\n" if dir_tree else ""

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
```diff
{diff_text}
```

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


def build_file_diff_prompt(
    file_path: str,
    diff_text: str,
    project_name: str | None = None,
    mode: str = "deep",
) -> str:
    """Build prompt for single-file diff code inspection (deep vs compact)."""
    clean_mode = "deep" if str(mode).lower() == "deep" else "compact"

    if clean_mode == "deep":
        return f"""You are a principal software engineer and expert code reviewer.
Perform an in-depth, exhaustive architectural and implementation code inspection of this single file diff recorded during a development burst.

File: {file_path}
Project: {project_name or 'Software Workspace'}
Analysis Mode: DEEP

Diff:
```diff
{diff_text}
```

Instructions:
Provide a rigorous, in-depth code inspection covering four essential pillars:
1. Architectural Role: Why this file exists, its component responsibilities, and its specific purpose in this burst.
2. Behavioral Delta: Detailed breakdown of what was removed, what was added, and line-by-line implementation intent. Trace algorithms, execution branching, and state transitions.
3. Symbol & Interface Mutations: Function/class/method signature changes, modified parameters, type alterations, and interface contracts.
4. Risk & Edge Cases: Regression risks, null-handling, concurrency, boundary conditions, and potential breaking changes. Assign an overall risk rating: LOW, MEDIUM, or HIGH.

Respond with ONLY a valid, parseable JSON object matching this schema:
{{
  "file_path": "{file_path}",
  "summary": "1-2 sentence architectural overview of changes in this file and its role in the burst",
  "why_modified": "Exhaustive breakdown covering Architectural Role, Behavioral Delta (what was removed vs added and intent), and State Mutations",
  "changed_symbols": ["List of changed classes, functions, methods, or interfaces with signature details and state impacts"],
  "breaking_changes": ["List of potential breaking changes, regression risks, null-handling vulnerabilities, or 'None detected'"],
  "risk_level": "LOW | MEDIUM | HIGH",
  "mode": "deep"
}}"""

    return f"""You are a senior software engineer.
Provide a concise, high-speed summary of this file diff.

File: {file_path}
Project: {project_name or 'Software Workspace'}
Analysis Mode: COMPACT

Diff:
```diff
{diff_text}
```

Instructions:
1. Summarize key changes in 2-3 concise bullet points.
2. List modified symbols (classes, functions, constants).
3. Flag whether there are any breaking changes.
4. Assign an overall risk rating: LOW, MEDIUM, or HIGH.

Respond with ONLY a valid, parseable JSON object matching this schema:
{{
  "file_path": "{file_path}",
  "summary": ["Bullet 1 describing key change", "Bullet 2 describing key change"],
  "why_modified": "Concise 1-sentence modification rationale",
  "changed_symbols": ["List of modified symbols"],
  "breaking_changes": ["List of breaking changes or 'None detected'"],
  "risk_level": "LOW | MEDIUM | HIGH",
  "mode": "compact"
}}"""

