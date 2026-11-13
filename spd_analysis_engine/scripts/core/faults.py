"""
scripts/core/faults.py
======================
Deterministic fault-injection framework for the SPD Analysis Engine.
Controlled via the SPD_TEST_FAULTS environment variable.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)


class InjectedFaultError(RuntimeError):
    """Raised when an injected fault triggers in 'raise' mode."""
    pass


def normalize_fault_name(name: str) -> str:
    """Normalize fault name to lowercase with underscores."""
    return str(name).strip().lower().replace("-", "_").replace(" ", "_")


def is_fault_active(fault_point: str) -> bool:
    """Check if the given fault point is active in SPD_TEST_FAULTS."""
    active = os.environ.get("SPD_TEST_FAULTS", "")
    if not active:
        return False
    targets = {normalize_fault_name(f) for f in active.split(",") if f.strip()}
    norm_point = normalize_fault_name(fault_point)
    return norm_point in targets or "all" in targets


def check_fault(fault_point: str) -> None:
    """
    Check if SPD_TEST_FAULTS requests a crash at fault_point.
    If active, triggers either an exit or an exception based on SPD_TEST_FAULTS_ACTION.
    """
    active = os.environ.get("SPD_TEST_FAULTS", "")
    if not active:
        return

    targets = [normalize_fault_name(f) for f in active.split(",") if f.strip()]
    norm_point = normalize_fault_name(fault_point)

    if norm_point in targets or "all" in targets:
        action = os.environ.get("SPD_TEST_FAULTS_ACTION", "exit").strip().lower()
        logger.critical("[FAULT_INJECTION] Injected fault triggered at: '%s' (action=%s)", fault_point, action)

        # Handle one-shot fault injection
        if os.environ.get("SPD_TEST_FAULTS_ONCE") == "1":
            remaining = [f for f in targets if f != norm_point]
            os.environ["SPD_TEST_FAULTS"] = ",".join(remaining)

        if action == "raise":
            raise InjectedFaultError(f"Injected fault crash at '{fault_point}'")
        else:
            exit_code = int(os.environ.get("SPD_TEST_FAULTS_EXIT_CODE", "77"))
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(exit_code)


@contextmanager
def fault_injection(
    fault_point: str, action: str = "raise", once: bool = True, exit_code: int = 77
) -> Generator[None, None, None]:
    """Scoped context manager for test-driven fault injection."""
    old_faults = os.environ.get("SPD_TEST_FAULTS")
    old_action = os.environ.get("SPD_TEST_FAULTS_ACTION")
    old_once = os.environ.get("SPD_TEST_FAULTS_ONCE")
    old_code = os.environ.get("SPD_TEST_FAULTS_EXIT_CODE")

    os.environ["SPD_TEST_FAULTS"] = fault_point
    os.environ["SPD_TEST_FAULTS_ACTION"] = action
    if once:
        os.environ["SPD_TEST_FAULTS_ONCE"] = "1"
    os.environ["SPD_TEST_FAULTS_EXIT_CODE"] = str(exit_code)

    try:
        yield
    finally:
        if old_faults is not None:
            os.environ["SPD_TEST_FAULTS"] = old_faults
        else:
            os.environ.pop("SPD_TEST_FAULTS", None)

        if old_action is not None:
            os.environ["SPD_TEST_FAULTS_ACTION"] = old_action
        else:
            os.environ.pop("SPD_TEST_FAULTS_ACTION", None)

        if old_once is not None:
            os.environ["SPD_TEST_FAULTS_ONCE"] = old_once
        else:
            os.environ.pop("SPD_TEST_FAULTS_ONCE", None)

        if old_code is not None:
            os.environ["SPD_TEST_FAULTS_EXIT_CODE"] = old_code
        else:
            os.environ.pop("SPD_TEST_FAULTS_EXIT_CODE", None)
