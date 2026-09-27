"""
SPD Analysis Engine – scripts package
======================================
Exposes the public API surface used by start.py, tests, and future
WebSocket / REST endpoints so that every feature can be driven either
from the CLI *or* from Python code.

Exported symbols
----------------
SessionDB       – SQLite WAL-backed database manager (storage_rotator)
StorageRotator  – Lifecycle directory rotator (storage_rotator)
Supervisor      – Multi-worker process supervisor (orchestrator)
"""

from .storage_rotator import SessionDB, StorageRotator  # noqa: F401
from .orchestrator import Supervisor  # noqa: F401
from .diff_calc import DiffCalculator  # noqa: F401
from .event_bundler import EventBundler  # noqa: F401
from .watcher_fs import ProjectWatcher  # noqa: F401
from .web_server import EngineWebServer  # noqa: F401
from .git_shadow import ShadowGit, GitCredentialManager  # noqa: F401
from .watcher_proc import ProcessInspector  # noqa: F401
from .shell_interceptor import ExecutionTracker  # noqa: F401
from .ai_analyzer import AISynthesizer  # noqa: F401

__all__ = [
    "SessionDB",
    "StorageRotator",
    "Supervisor",
    "DiffCalculator",
    "EventBundler",
    "ProjectWatcher",
    "EngineWebServer",
    "ShadowGit",
    "GitCredentialManager",
    "ProcessInspector",
    "ExecutionTracker",
    "AISynthesizer",
]
