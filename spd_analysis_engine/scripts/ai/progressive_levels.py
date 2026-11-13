"""
scripts/ai/progressive_levels.py
================================
Progressive multi-level AI analysis prompt generation and response parsing.

Levels:
- L1 Quick: High-level executive intent and category
- L2 Detailed: Code mechanics and per-file modifications
- L3 Architecture: System contracts, coupling, and boundary implications
- L4 Risk/Impact: Regressions, failure modes, and rollback feasibility

Higher levels receive lower-level output plus the diff/context with an explicit:
"expand, don't restate" instruction.
L1/L2/L3/L4 are separate stored analyses.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .identity import ProgressiveLevel, EXPAND_INSTRUCTION

logger = logging.getLogger(__name__)


def build_progressive_prompt(
    level: str,
    diff_text: str,
    context: dict[str, Any] | None = None,
    lower_level_output: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Build (system_instruction, user_prompt) for the requested progressive level.
    Injects lower-level output with the mandatory 'expand, don't restate' directive.
    """
    ctx = context or {}
    project = ctx.get("project_name", ctx.get("project_slug", "Project"))
    target_id = ctx.get("target_id", "Current Change")
    truncated_diff = diff_text[:12000] if len(diff_text) > 12000 else diff_text

    # Extract lower level summary text if provided
    lower_summary = ""
    if lower_level_output:
        if isinstance(lower_level_output, dict):
            lower_summary = (
                lower_level_output.get("intent")
                or lower_level_output.get("summary")
                or lower_level_output.get("body")
                or json.dumps(lower_level_output, indent=2)
            )
        else:
            lower_summary = str(lower_level_output)

    if level == ProgressiveLevel.L1:
        sys_prompt = (
            "You are an expert software telemetry analyst. Provide an L1 Quick executive summary "
            "of code changes. Be concise (under 150 words). Output valid JSON with fields: "
            "'intent' (str), 'category' (feat|fix|refactor|test|chore), 'tags' (list[str]), "
            "'summary' (str)."
        )
        user_prompt = (
            f"Project: {project}\nTarget: {target_id}\n\n"
            f"Diff:\n```diff\n{truncated_diff}\n```\n\n"
            "Produce an L1 Quick executive summary in JSON format."
        )

    elif level == ProgressiveLevel.L2:
        sys_prompt = (
            f"You are an expert code reviewer. Provide an L2 Detailed code analysis.\n"
            f"CRITICAL DIRECTIVE: {EXPAND_INSTRUCTION}.\n"
            f"Build upon the existing L1 summary with per-file code mechanics, function signatures, "
            f"and functional logic without repeating the high-level summary.\n"
            f"Output valid JSON with fields: 'intent' (str), 'file_breakdown' (list[dict]), "
            f"'functional_mechanics' (str), 'key_changes' (list[str])."
        )
        base_block = f"Prior L1 Analysis:\n{lower_summary}\n\n" if lower_summary else ""
        user_prompt = (
            f"Project: {project}\nTarget: {target_id}\n\n"
            f"{base_block}"
            f"Diff:\n```diff\n{truncated_diff}\n```\n\n"
            f"Directive: {EXPAND_INSTRUCTION}.\n"
            f"Produce an L2 Detailed technical analysis in JSON format."
        )

    elif level == ProgressiveLevel.L3:
        sys_prompt = (
            f"You are a principal software architect. Provide an L3 Architecture analysis.\n"
            f"CRITICAL DIRECTIVE: {EXPAND_INSTRUCTION}.\n"
            f"Evaluate architectural invariants, module boundaries, system contracts, data flow, "
            f"and dependency coupling without repeating file-level implementation mechanics.\n"
            f"Output valid JSON with fields: 'architecture_impact' (str), 'layer_boundaries' (str), "
            f"'contract_mutations' (list[str]), 'dependency_shifts' (list[str])."
        )
        base_block = f"Prior Detailed Analysis:\n{lower_summary}\n\n" if lower_summary else ""
        user_prompt = (
            f"Project: {project}\nTarget: {target_id}\n\n"
            f"{base_block}"
            f"Diff:\n```diff\n{truncated_diff}\n```\n\n"
            f"Directive: {EXPAND_INSTRUCTION}.\n"
            f"Produce an L3 Architecture analysis in JSON format."
        )

    elif level == ProgressiveLevel.L4:
        sys_prompt = (
            f"You are a site reliability and security principal engineer. Provide an L4 Risk/Impact analysis.\n"
            f"CRITICAL DIRECTIVE: {EXPAND_INSTRUCTION}.\n"
            f"Evaluate regression surfaces, potential breaking changes, concurrency risks, failure modes, "
            f"test coverage gaps, and rollback feasibility without repeating architectural overviews.\n"
            f"Output valid JSON with fields: 'risk_level' (LOW|MEDIUM|HIGH|CRITICAL), "
            f"'regression_risks' (list[str]), 'breaking_changes' (list[str]), "
            f"'rollback_feasibility' (str), 'test_gaps' (list[str])."
        )
        base_block = f"Prior Architecture Analysis:\n{lower_summary}\n\n" if lower_summary else ""
        user_prompt = (
            f"Project: {project}\nTarget: {target_id}\n\n"
            f"{base_block}"
            f"Diff:\n```diff\n{truncated_diff}\n```\n\n"
            f"Directive: {EXPAND_INSTRUCTION}.\n"
            f"Produce an L4 Risk and Impact assessment in JSON format."
        )

    else:
        # Default fallback
        sys_prompt = "You are a code analyst. Output JSON summary."
        user_prompt = f"Analyze:\n{truncated_diff}\n"

    return sys_prompt, user_prompt


def parse_progressive_result(level: str, raw_data: Any) -> dict[str, Any]:
    """Normalize raw model response into standard level dict."""
    if isinstance(raw_data, dict):
        res = dict(raw_data)
    elif isinstance(raw_data, str):
        try:
            res = json.loads(raw_data)
        except Exception:
            res = {"raw_text": raw_data}
    else:
        res = {"raw": str(raw_data)}

    res["progressive_level"] = level
    return res
