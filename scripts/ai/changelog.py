"""
scripts/ai/changelog.py
=======================
Session changelog generation and conventional commit synthesis.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_header() -> str:
    """Return standard release header timestamp in IST (e.g. 2026-09-24 23:14 IST)."""
    return datetime.now(_IST).strftime("%Y-%m-%d %H:%M IST")


def _fallback_changelog(
    files: list[str],
    events: list[dict[str, Any]],
    reason: str = "offline",
) -> dict[str, Any]:
    """Generate a structured heuristic Conventional Commit & CHANGELOG when AI is offline."""
    ts_str = _now_ist_header()

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
    session_events: list[dict[str, Any]],
    diffs: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    synthesizer: Any = None,
) -> dict[str, Any]:
    """
    Analyze all prompt bursts and diffs across a monitoring session to produce
    a Conventional Commit title, description, and markdown changelog entry.
    """
    if synthesizer is None:
        try:
            from .synthesizer import AISynthesizer
        except (ImportError, ModuleNotFoundError):
            try:
                from spd_analysis_engine.scripts.ai.synthesizer import AISynthesizer
            except (ImportError, ModuleNotFoundError):
                from scripts.ai.synthesizer import AISynthesizer
        synthesizer = AISynthesizer()

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
        return _fallback_changelog([], session_events, reason="No files modified in session")

    # 2. Check Cache using session diff hash
    aggregated_diff = "\n\n".join(diff_snippets).strip()
    session_hash = "session_" + synthesizer.compute_diff_hash(aggregated_diff or ", ".join(files_list))
    cached = synthesizer.get_cached_analysis(session_hash)
    if cached:
        return cached

    # 3. Build Prompt
    event_summaries = [f"- Burst #{e.get('id', '?')}: {e.get('summary', 'burst edit')}" for e in session_events if e.get("event_type") == "EDIT"]
    event_text = "\n".join(event_summaries[:15]) or "- Multi-file prompt burst edits"

    chunked_diffs = synthesizer.chunk_diff(aggregated_diff, max_lines=250) if aggregated_diff else "Diff not available"

    prompt = f"""You are a senior software architect and release engineer writing a Conventional Commit and CHANGELOG entry for a software session.
Analyze the following session event history, modified files, and diff excerpts to generate a high quality release summary.

Session Metadata:
- Timestamp: {ts_str}
- Files touched: {', '.join(files_list[:25])}
- Event progression:
{event_text}

Diff Excerpts:
```diff
{chunked_diffs}
```

Instructions:
Respond with ONLY a valid, parseable JSON object without markdown fences, matching this exact schema:
{{
  "commit_title": "<Conventional commit message under 72 chars with type and scope, e.g. feat(core): implement user authentication and oauth2 flow>",
  "commit_body": "<Bulleted list of high-level modifications grouped by component>",
  "changelog_entry": "### [{ts_str}] <commit_title>\\n\\n#### Key Highlights\\n- <bullet 1>\\n- <bullet 2>\\n\\n#### Files Modified\\n{', '.join(files_list[:10])}\\n\\n#### Functional & Architectural Impact\\n<Detailed paragraph explaining concrete capabilities gained, bugs resolved, or behavioral guarantees achieved.>"
}}
"""

    # 4. Resolve providers
    cfg = synthesizer.load_config(synthesizer.engine_root)
    pref_provider = (provider or cfg.get("preferred_provider") or cfg.get("default_provider", "gemini")).lower()

    if pref_provider in ("heuristic", "none"):
        fallback = _fallback_changelog(files_list, session_events, reason="heuristic_mode")
        fallback["provider"] = "heuristic"
        fallback["diff_hash"] = session_hash
        synthesizer.save_cached_analysis(session_hash, fallback)
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
                rate_status = synthesizer.rate_limiter.get_status()
                if rate_status["cooling_down"] and rate_status["cooldown_remaining_s"] > 3.0:
                    logger.info("Gemini is cooling down (%.1fs remaining); skipping changelog to next provider.", rate_status["cooldown_remaining_s"])
                    last_error = f"Gemini cooling down ({rate_status['cooldown_remaining_s']}s remaining)"
                    continue
                logger.info("Generating session changelog via Gemini API...")
                raw_res = synthesizer._call_gemini(prompt)
            elif prov == "ollama":
                logger.info("Generating session changelog via local Ollama...")
                raw_res = synthesizer._call_ollama(prompt)
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
        result = _fallback_changelog(files_list, session_events, reason=f"Providers unreachable ({last_error})")

    result["diff_hash"] = session_hash
    synthesizer.save_cached_analysis(session_hash, result)
    return result


class ChangelogMixin:
    """Mixin providing session changelog generation methods for AISynthesizer."""

    def _fallback_changelog(
        self,
        files: list[str],
        events: list[dict[str, Any]],
        reason: str = "offline",
    ) -> dict[str, Any]:
        return _fallback_changelog(files, events, reason=reason)

    def generate_session_changelog(
        self,
        session_events: list[dict[str, Any]],
        diffs: list[dict[str, Any]] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        return generate_session_changelog(session_events, diffs=diffs, provider=provider, synthesizer=self)


__all__ = [
    "generate_session_changelog",
    "_fallback_changelog",
    "ChangelogMixin",
]
