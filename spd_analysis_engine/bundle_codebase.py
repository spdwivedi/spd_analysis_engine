"""
bundle_codebase.py
Compiles all active SPD Analysis Engine source code into a structured Markdown document.
Uses strict path segment filtering to avoid false exclusions on Windows.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent
OUTPUT_FILE = ENGINE_ROOT / "codebase_bundle.md"

TARGET_DIRS = ["scripts", "web", "tests"]
ROOT_FILES = [
    "start.py",
    "requirements.txt",
    "audit_engine.ps1",
    "ai_data/models_config.example.json",
    "ai_data/git_config.example.json",
]

# Directories excluded anywhere in the tree (VCS, virtualenvs, bytecode, test sandboxes)
ANYWHERE_EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    "shadow_git",
    ".venvconda",
    ".venv",
    "venv",
    "mock_workspace",
    ".pytest_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
    "node_modules",
}

# Runtime and data directories excluded only at the engine root level
ROOT_EXCLUDE_DIRS = {
    "current",
    "last_run",
    "history",
    "trash",
    "_backup_monoliths",
    "cache",
}

EXCLUDE_FILE_NAMES = {
    "codebase_bundle.md",
    "pipeline_bundle.md",
    "project_tree.txt",
    "project_tree_2.txt",
    "project_tree_3.txt",
    "project_tree_4.txt",
    "project_tree_spd_engine.txt",
    "all_file_paths.txt",
    "models_config.json",
    "git_config.json",
}

EXCLUDE_EXTENSIONS = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".pid",
    ".lock",
    ".wal",
    ".shm",
    ".stale",
    ".tmp",
    ".key",
    ".pem",
    ".pfx",
}

TARGET_EXTENSIONS = {".py", ".js", ".html", ".css", ".json", ".ps1", ".txt"}


def get_lang_tag(ext: str) -> str:
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".html": "html",
        ".css": "css",
        ".json": "json",
        ".ps1": "powershell",
    }
    return mapping.get(ext, "text")


def should_exclude(file_path: Path) -> bool:
    name = file_path.name
    # Exclude specific files and credential/secret patterns
    if name in EXCLUDE_FILE_NAMES:
        return True
    if name.startswith(".env") or name.endswith(".env"):
        return True
    if file_path.suffix.lower() in EXCLUDE_EXTENSIONS:
        return True

    # Check anywhere excluded directories
    for part in file_path.parts:
        if part in ANYWHERE_EXCLUDE_DIRS:
            return True

    # Check root-level data directories
    try:
        rel = file_path.resolve().relative_to(ENGINE_ROOT.resolve())
        if rel.parts and rel.parts[0] in ROOT_EXCLUDE_DIRS:
            return True
        if len(rel.parts) >= 2 and rel.parts[0] == "ai_data" and rel.parts[1] == "cache":
            return True
    except ValueError:
        pass

    return False


def collect_files() -> list[dict]:
    collected = []

    # 1. Root files
    for rf in ROOT_FILES:
        fp = ENGINE_ROOT / rf
        if fp.is_file() and not should_exclude(fp):
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
                lines = content.count("\n") + 1 if content else 0
                size_kb = round(fp.stat().st_size / 1024, 2)
                collected.append({
                    "rel_path": rf.replace("/", "\\"),
                    "full_path": fp,
                    "ext": fp.suffix.lower(),
                    "lines": lines,
                    "size_kb": size_kb,
                    "content": content,
                })
            except Exception as e:
                print(f"[WARN] Could not read {rf}: {e}")

    # 2. Target directories
    for td in TARGET_DIRS:
        dp = ENGINE_ROOT / td
        if not dp.is_dir():
            continue
        for root, dirs, files in os.walk(dp):
            # Prune excluded directories in-place so os.walk doesn't descend into them
            dirs[:] = [d for d in dirs if d not in ANYWHERE_EXCLUDE_DIRS and not should_exclude(Path(root) / d)]

            for file in sorted(files):
                fp = Path(root) / file
                if fp.suffix.lower() not in TARGET_EXTENSIONS:
                    continue
                if should_exclude(fp):
                    continue

                try:
                    content = fp.read_text(encoding="utf-8", errors="replace")
                    lines = content.count("\n") + 1 if content else 0
                    size_kb = round(fp.stat().st_size / 1024, 2)
                    rel = str(fp.relative_to(ENGINE_ROOT))
                    collected.append({
                        "rel_path": rel,
                        "full_path": fp,
                        "ext": fp.suffix.lower(),
                        "lines": lines,
                        "size_kb": size_kb,
                        "content": content,
                    })
                except Exception as e:
                    print(f"[WARN] Could not read {fp}: {e}")

    collected.sort(key=lambda x: x["rel_path"].lower())
    return collected


def main():
    print("=" * 60)
    print("   SPD ANALYSIS ENGINE -- CODEBASE BUNDLER (PYTHON)   ")
    print("=" * 60)

    files = collect_files()
    total_lines = sum(f["lines"] for f in files)
    total_kb = sum(f["size_kb"] for f in files)
    total_mb = round(total_kb / 1024, 2)

    print(f"Found {len(files)} relevant source code files.")
    print(f"Total lines: {total_lines} | Total size: {total_mb} MB")
    print("Compiling Markdown document...")

    output_lines = [
        "# SPD Analysis Engine — Complete Codebase Source Bundle",
        f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Target Workspace: {ENGINE_ROOT}",
        "",
        "---",
        "",
        "## 1. Codebase Summary & File Index",
        "",
        "| Relative File Path | Language | Line Count | Size (KB) | Modularity Status |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for f in files:
        status = "✓ Micro (<200L)"
        if f["lines"] > 500:
            status = "⚠️ Monolith (>500L)"
        elif f["lines"] > 200:
            status = "Medium (200-500L)"

        ext_clean = f["ext"].lstrip(".")
        output_lines.append(
            f"| `{f['rel_path']}` | {ext_clean} | {f['lines']} | {f['size_kb']} KB | {status} |"
        )

    output_lines.extend([
        "",
        f"**Total Files:** {len(files)} | **Total Code Lines:** {total_lines} | **Total Uncompressed Size:** {total_mb} MB",
        "",
        "---",
        "",
        "## 2. Complete Source Code",
        "",
    ])

    for i, f in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] Bundling: {f['rel_path']} ({f['lines']} lines)")
        ext_clean = f["ext"].lstrip(".")
        output_lines.append(f"### [{i}/{len(files)}] File: `{f['rel_path']}`")
        output_lines.append(f"- **Lines:** {f['lines']} | **Size:** {f['size_kb']} KB | **Type:** {ext_clean}")
        output_lines.append("")

        lang = get_lang_tag(f["ext"])
        output_lines.append(f"```{lang}")

        # Sanitize internal triple backticks to avoid breaking markdown codeblocks
        safe_content = f["content"].replace("```", "`` `")
        output_lines.append(safe_content.rstrip())
        output_lines.append("```")
        output_lines.append("")
        output_lines.append("---")
        output_lines.append("")

    OUTPUT_FILE.write_text("\n".join(output_lines), encoding="utf-8")
    final_mb = round(OUTPUT_FILE.stat().st_size / (1024 * 1024), 2)

    print("=" * 60)
    print(f"SUCCESS! Codebase bundled into:")
    print(f"  {OUTPUT_FILE} ({final_mb} MB)")
    print("=" * 60)


if __name__ == "__main__":
    main()