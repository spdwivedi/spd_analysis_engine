"""
scripts/ai/error_handling.py
============================
Error classification and retry policy engine for controller-managed AI execution.

Retry rules:
- 429/503 -> retry with backoff
- auth errors -> no automatic retry
- JSON parse error -> one repair retry
- timeout -> one smaller-context retry
- permanent failure -> failed state + Retry action
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Error Classes
# -----------------------------------------------------------------------------

class AIError(Exception):
    """Base class for all AI execution errors."""
    pass


class RateLimitError(AIError):
    """HTTP 429, 503, quota exhaustion, or temporary provider unavailability."""
    pass


class AuthenticationError(AIError):
    """Invalid, missing, or unauthorized API keys (HTTP 401, 403)."""
    pass


class JSONParseError(AIError):
    """Malformed or non-JSON output returned by the model."""
    pass


import builtins


class TimeoutError(AIError, builtins.TimeoutError):
    """Execution deadline exceeded or socket connection timed out."""
    pass


class PermanentError(AIError):
    """Unrecoverable failure requiring manual intervention."""
    pass


class ConsentRequiredError(AIError):
    """Cloud analysis requires per-project consent/enable setting."""
    pass


# -----------------------------------------------------------------------------
# Retry Plan
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class RetryPlan:
    """Actionable decision regarding how to handle an AI failure."""

    should_retry: bool
    action: str  # "backoff" | "repair_json" | "smaller_context" | "fail"
    delay_s: float
    error_class: str
    reason: str
    can_manual_retry: bool = True


# -----------------------------------------------------------------------------
# Classifier & Evaluator
# -----------------------------------------------------------------------------

def classify_ai_exception(exc: Exception) -> tuple[str, Exception]:
    """
    Classify an arbitrary exception into a standard error class name and typed exception.
    """
    if isinstance(exc, RateLimitError):
        return "RateLimitError", exc
    if isinstance(exc, AuthenticationError):
        return "AuthenticationError", exc
    if isinstance(exc, JSONParseError):
        return "JSONParseError", exc
    if isinstance(exc, (TimeoutError, TimeoutError)):
        return "TimeoutError", exc
    if isinstance(exc, PermanentError):
        return "PermanentError", exc
    if isinstance(exc, ConsentRequiredError):
        return "ConsentRequiredError", exc

    if isinstance(exc, json.JSONDecodeError):
        return "JSONParseError", JSONParseError(str(exc))

    msg = str(exc).lower()

    # 0. Consent errors
    if any(k in msg for k in ("consent", "cloud ai is disabled", "cloud ai analysis requires")):
        return "ConsentRequiredError", ConsentRequiredError(str(exc))

    # 1. Auth errors
    if any(k in msg for k in ("401", "403", "unauthenticated", "invalid api key", "api_key_invalid", "permission denied", "forbidden", "bad credentials", "not configured")):
        return "AuthenticationError", AuthenticationError(str(exc))

    # 2. Rate limits & temporary unavailable
    if any(k in msg for k in ("429", "503", "rate limit", "resource_exhausted", "quota", "high demand", "temporarily unavailable", "service unavailable")):
        return "RateLimitError", RateLimitError(str(exc))

    # 3. Timeouts
    if any(k in msg for k in ("timed out", "timeout", "deadline", "operation timed out")):
        return "TimeoutError", TimeoutError(str(exc))

    # 4. JSON parse issues
    if any(k in msg for k in ("json", "expecting value", "delimiter", "unterminated string")):
        return "JSONParseError", JSONParseError(str(exc))

    return "PermanentError", PermanentError(str(exc))


def evaluate_retry_plan(
    error_class: str,
    retry_count: int,
    max_retries: int = 3,
) -> RetryPlan:
    """
    Evaluate the retry rule for a given error class and attempt count.

    Rules:
    - 429/503 -> retry with backoff
    - auth errors -> no automatic retry
    - JSON parse error -> one repair retry
    - timeout -> one smaller-context retry
    - permanent failure -> failed state + Retry action
    """
    # Rule 0: Consent required -> no automatic retry
    if error_class == "ConsentRequiredError":
        return RetryPlan(
            should_retry=False,
            action="fail",
            delay_s=0.0,
            error_class=error_class,
            reason="Cloud analysis requires per-project consent/enable setting; no automatic retry.",
            can_manual_retry=True,
        )

    # Rule 1: Auth errors -> no automatic retry
    if error_class == "AuthenticationError":
        return RetryPlan(
            should_retry=False,
            action="fail",
            delay_s=0.0,
            error_class=error_class,
            reason="Authentication errors require valid credentials; no automatic retry.",
            can_manual_retry=True,
        )

    # Rule 2: 429/503 -> retry with backoff
    if error_class == "RateLimitError":
        if retry_count < max_retries:
            delay = min(30.0, 1.5 * (2 ** retry_count))
            return RetryPlan(
                should_retry=True,
                action="backoff",
                delay_s=delay,
                error_class=error_class,
                reason=f"Rate limit / 503 backoff delay of {delay:.1f}s (retry {retry_count + 1}/{max_retries}).",
                can_manual_retry=True,
            )
        return RetryPlan(
            should_retry=False,
            action="fail",
            delay_s=0.0,
            error_class=error_class,
            reason=f"Exhausted {max_retries} rate limit retries.",
            can_manual_retry=True,
        )

    # Rule 3: JSON parse error -> exactly one repair retry
    if error_class == "JSONParseError":
        if retry_count < 1:
            return RetryPlan(
                should_retry=True,
                action="repair_json",
                delay_s=0.2,
                error_class=error_class,
                reason="Malformed JSON output; executing one repair retry.",
                can_manual_retry=True,
            )
        return RetryPlan(
            should_retry=False,
            action="fail",
            delay_s=0.0,
            error_class=error_class,
            reason="JSON repair retry failed.",
            can_manual_retry=True,
        )

    # Rule 4: Timeout -> exactly one smaller-context retry
    if error_class == "TimeoutError":
        if retry_count < 1:
            return RetryPlan(
                should_retry=True,
                action="smaller_context",
                delay_s=0.5,
                error_class=error_class,
                reason="Execution timed out; executing one smaller-context retry.",
                can_manual_retry=True,
            )
        return RetryPlan(
            should_retry=False,
            action="fail",
            delay_s=0.0,
            error_class=error_class,
            reason="Smaller-context retry also timed out.",
            can_manual_retry=True,
        )

    # Rule 5: Permanent failure -> failed state + manual Retry action available
    return RetryPlan(
        should_retry=False,
        action="fail",
        delay_s=0.0,
        error_class="PermanentError",
        reason="Permanent or unclassified failure; requires explicit Retry action.",
        can_manual_retry=True,
    )
