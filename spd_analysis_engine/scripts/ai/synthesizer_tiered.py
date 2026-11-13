"""
scripts/ai/synthesizer_tiered.py
=================================
Progressive and tiered milestone synthesizer:
- Tier 1: Zero-AI deterministic conventional commit generator & root commit guard.
- Tier 2: Low-token condensed context synthesis (<800 tokens) via Gemini or Ollama.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import AIConfig
from .provider_gemini import call_gemini
from .provider_ollama import call_ollama
from ..git.utils import _NO_WINDOW_FLAG

logger = logging.getLogger(__name__)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shutil.which("git") or "git", *args], cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, creationflags=_NO_WINDOW_FLAG, timeout=15,
    )


def _classify_conventional_tag(touched_paths: list[str]) -> str:
    lower = [p.lower() for p in touched_paths]
    if any("test" in p for p in lower): return "test(engine)"
    if any(p.endswith((".md", ".txt", ".rst")) or "plan" in p or "doc" in p for p in lower): return "docs(spec)"
    if any(p.endswith((".json", ".yaml", ".yml", ".toml", ".gitignore")) or "config" in p for p in lower): return "chore(config)"
    if any("fix" in p or "bug" in p or "patch" in p for p in lower): return "fix(core)"
    return "feat(core)"


def _parse_ai_json(raw_text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def tier1_heuristic_synthesize(
    project_slug: str = "",
    unpushed_bursts: list[int] | None = None,
    is_root: bool = False,
    touched_files: list[str] | None = None,
    failover_reason: str | None = None,
) -> dict[str, Any]:
    """Tier 1: Zero-AI deterministic conventional commit generator."""
    slug = project_slug or "project"
    files, burst_ids = touched_files or [], unpushed_bursts or []

    if is_root:
        dirs = sorted({p.split("/")[0] for p in files if "/" in p}) or ["root"]
        return {
            "title": f"feat(scaffold): initialize {slug} architecture and baseline specifications",
            "body": f"Initial project commit establishing baseline workspace structure.\n- Categories: {', '.join(dirs[:5])}\n- Total files: {len(files)}",
            "changelog": f"## [{slug}] Initial Architecture Baseline\n- Initialized {len(files)} files across: {', '.join(dirs)}",
            "mode_used": "tier1_root",
            "provider_used": failover_reason or "Tier 1 Zero-AI Heuristic",
        }

    dominant_tag = _classify_conventional_tag(files)
    primary_name = Path(files[0]).name if files else slug
    return {
        "title": f"{dominant_tag}: update {primary_name} ({len(files)} file{'s' if len(files) != 1 else ''})",
        "body": f"Automated commit for burst {burst_ids}.\nTouched files:\n" + "\n".join(f"- {f}" for f in files[:15]),
        "changelog": f"### Changes in {slug}\n" + "\n".join(f"- {f}" for f in files[:10]),
        "mode_used": "tier1_heuristic",
        "provider_used": failover_reason or "Tier 1 Zero-AI Heuristic",
    }


def synthesize_milestone(
    burst_ids: list[int] | None = None,
    mode: str = "tiered",
    project_slug: str = "",
    target_path: Path | str = "",
    engine_root: Path | str | None = None,
) -> dict[str, Any]:
    """Synthesize commit title, body, and changelog using Tier 1 (Heuristic) or Tier 2 (AI)."""
    target = Path(target_path).resolve() if target_path else Path.cwd()
    slug = project_slug or target.name or "project"

    is_root = False
    if (target / ".git").exists():
        is_root = (_run_git(["rev-parse", "--verify", "HEAD"], target).returncode != 0)

    status_res = _run_git(["status", "--porcelain"], target)
    touched_files: list[str] = []
    if status_res.returncode == 0:
        for line in status_res.stdout.splitlines():
            line = line.strip()
            if len(line) > 3:
                touched_files.append(line[3:].strip().strip('"'))

    if is_root and mode != "tier2_ai":
        return tier1_heuristic_synthesize(slug, burst_ids, is_root=True, touched_files=touched_files)
    if mode == "tier1" or not touched_files:
        return tier1_heuristic_synthesize(slug, burst_ids, is_root=is_root, touched_files=touched_files)

    # Tier 2: Progressive AI Synthesis (<800 tokens condensed payload)
    try:
        cfg = AIConfig.load_config(engine_root)
        active_provider = cfg.get("active_provider", "gemini")
        diff_stat = _run_git(["diff", "--stat"], target).stdout.strip()
        diff_summary = diff_stat.splitlines()[-1] if diff_stat else "No diff summary"

        snippet = ""
        if touched_files:
            first_path = target / touched_files[0]
            if first_path.is_file():
                try:
                    snippet = "\n".join(first_path.read_text(encoding="utf-8", errors="replace").splitlines()[:20])
                except OSError:
                    pass

        condensed_payload = {
            "project": slug, "burst_ids": burst_ids or [], "total_files": len(touched_files),
            "files_sample": touched_files[:10], "diff_stat": diff_summary, "entrypoint_head": snippet,
        }
        prompt = (
            "You are a Conventional Commit generator. Return ONLY a JSON object with keys: "
            "'title' (feat(scope): ... or fix(scope): ... max 72 chars), 'body' (2-3 concise sentences), "
            f"'changelog' (markdown bullet points).\nContext:\n{json.dumps(condensed_payload, indent=2)}"
        )

        if active_provider == "ollama":
            res = call_ollama(prompt, cfg.get("ollama", {}), _parse_ai_json)
        else:
            gem_cfg = dict(cfg.get("gemini", {}))
            gem_cfg["timeout_seconds"] = min(gem_cfg.get("timeout_seconds", 30), 4)
            res = call_gemini(prompt, gem_cfg, None, _parse_ai_json, fast_fail=True)

        if isinstance(res, dict) and res.get("title") and res.get("body"):
            return {
                "title": str(res["title"]).strip(),
                "body": str(res["body"]).strip(),
                "changelog": str(res.get("changelog") or "").strip(),
                "mode_used": "tier2_ai",
                "provider_used": res.get("provider", f"gemini:{gem_cfg.get('default_model', 'flash-lite')}"),
            }
    except Exception as exc:
        err_lower = str(exc).lower()
        if any(k in err_lower for k in ("503", "unavailable", "high demand", "capacity")):
            reason = "Tier 1 Heuristic (Gemini 503 Failover)"
        elif any(k in err_lower for k in ("429", "exhausted", "quota")):
            reason = "Tier 1 Heuristic (Gemini 429 Failover)"
        else:
            reason = "Tier 1 Heuristic (AI Failover)"
        logger.warning("AI synthesis exception (%s); circuit breaker engaged: %s", exc, reason)
        return tier1_heuristic_synthesize(slug, burst_ids, is_root, touched_files, failover_reason=reason)

    return tier1_heuristic_synthesize(slug, burst_ids, is_root=is_root, touched_files=touched_files)


__all__ = ["synthesize_milestone", "tier1_heuristic_synthesize"]
