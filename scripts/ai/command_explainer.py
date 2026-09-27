"""
scripts/ai/command_explainer.py
================================
Terminal command intent, technical purpose, and outcome explanation via AI.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _fallback_command_explanation(
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
    project_name: str,
    command_str: str,
    exit_code: int = 0,
    duration_s: float = 0.0,
    surrounding_burst_context: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    synthesizer: Any = None,
) -> dict[str, Any]:
    """
    Explain a terminal command execution using AI (Gemini or Ollama) with offline fallback.
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

    clean_cmd = command_str.strip()
    cache_key = "cmd_" + hashlib.sha256(f"{clean_cmd}_{exit_code}_{duration_s:.1f}".encode()).hexdigest()[:24]

    if not clean_cmd:
        res = _fallback_command_explanation(command_str, exit_code, duration_s, reason="empty_command")
        res["diff_hash"] = cache_key
        return res

    # 1. Check Cache
    cached = synthesizer.get_cached_analysis(cache_key, project_name=project_name)
    if cached:
        return cached

    # 2. Check Provider Preferences
    cfg = synthesizer.load_config(synthesizer.engine_root)
    pref_provider = (provider or cfg.get("preferred_provider") or cfg.get("default_provider", "gemini")).lower()

    if pref_provider in ("heuristic", "none"):
        fallback = _fallback_command_explanation(clean_cmd, exit_code, duration_s, reason="heuristic_mode")
        fallback["provider"] = "heuristic"
        fallback["diff_hash"] = cache_key
        synthesizer.save_cached_analysis(cache_key, fallback, project_name=project_name)
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
                rate_status = synthesizer.rate_limiter.get_status()
                if rate_status["cooling_down"] and rate_status["cooldown_remaining_s"] > 3.0:
                    logger.info("Gemini cooling down (%.1fs remaining); skipping to next provider.", rate_status["cooldown_remaining_s"])
                    last_error = f"Gemini cooling down ({rate_status['cooldown_remaining_s']}s remaining)"
                    continue
                logger.info("Explaining command via Gemini API...")
                raw_res = synthesizer._call_gemini(prompt)
            elif prov == "ollama":
                logger.info("Explaining command via local Ollama...")
                raw_res = synthesizer._call_ollama(prompt)
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
        result = _fallback_command_explanation(clean_cmd, exit_code, duration_s, reason=f"Providers unreachable ({last_error})")

    result["diff_hash"] = cache_key
    synthesizer.save_cached_analysis(cache_key, result, project_name=project_name)
    return result


class CommandExplainerMixin:
    """Mixin providing command explanation methods for AISynthesizer."""

    def _fallback_command_explanation(
        self,
        command_str: str,
        exit_code: int = 0,
        duration_s: float = 0.0,
        reason: str = "offline",
    ) -> dict[str, Any]:
        return _fallback_command_explanation(command_str, exit_code=exit_code, duration_s=duration_s, reason=reason)

    def explain_command(
        self,
        project_name: str,
        command_str: str,
        exit_code: int = 0,
        duration_s: float = 0.0,
        surrounding_burst_context: list[dict[str, Any]] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        return explain_command(
            project_name=project_name,
            command_str=command_str,
            exit_code=exit_code,
            duration_s=duration_s,
            surrounding_burst_context=surrounding_burst_context,
            provider=provider,
            synthesizer=self,
        )


__all__ = [
    "explain_command",
    "_fallback_command_explanation",
    "CommandExplainerMixin",
]
