"""
SPD Analysis Engine
===================
Canonical package namespace for the SPD Analysis Engine daemon.
"""
from __future__ import annotations

import sys

# Install legacy scripts facade finder immediately
from .scripts.facade import install_facade_finder, register_module_alias

install_facade_finder()

# Expose scripts subpackage
from . import scripts

# Alias root legacy scripts package in sys.modules
sys.modules.setdefault("scripts", scripts)

# Public high-level engine API
from .scripts.orchestrator import Supervisor
from .scripts.storage_rotator import StorageRotator, SessionDB
from .scripts.diff_calc import DiffCalculator
from .scripts.event_bundler import EventBundler
from .scripts.git_shadow import ShadowGit, GitCredentialManager

__all__ = [
    "Supervisor",
    "StorageRotator",
    "SessionDB",
    "DiffCalculator",
    "EventBundler",
    "ShadowGit",
    "GitCredentialManager",
    "scripts",
]
