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

from .facade import install_facade_finder, register_module_alias

install_facade_finder()
import sys as _sys
register_module_alias("scripts", _sys.modules.get(__name__))
register_module_alias("spd_analysis_engine.scripts", _sys.modules.get(__name__))

from spd_analysis_engine.scripts.storage_rotator import SessionDB, StorageRotator  # noqa: F401
from spd_analysis_engine.scripts.orchestrator import Supervisor  # noqa: F401
from spd_analysis_engine.scripts.diff_calc import DiffCalculator  # noqa: F401
from spd_analysis_engine.scripts.event_bundler import EventBundler  # noqa: F401
from spd_analysis_engine.scripts.watcher_fs import ProjectWatcher  # noqa: F401
from spd_analysis_engine.scripts.git_shadow import ShadowGit, GitCredentialManager  # noqa: F401
from spd_analysis_engine.scripts.watcher_proc import ProcessInspector  # noqa: F401
from spd_analysis_engine.scripts.shell_interceptor import ExecutionTracker  # noqa: F401

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
    "IgnorePolicy",
    "FileManifest",
    "FsEvent",
    "BurstAssembler",
    "ChangeProcessor",
]


def __getattr__(name: str):
    if name == "EngineWebServer":
        from .web_server import EngineWebServer
        return EngineWebServer
    if name == "AISynthesizer":
        from .ai_analyzer import AISynthesizer
        return AISynthesizer
    if name == "IgnorePolicy":
        from .storage.ignore_policy import IgnorePolicy
        return IgnorePolicy
    if name == "FileManifest":
        from .storage.manifest import FileManifest
        return FileManifest
    if name == "FsEvent":
        from .core.fs_event import FsEvent
        return FsEvent
    if name == "BurstAssembler":
        from .core.burst_assembler import BurstAssembler
        return BurstAssembler
    if name == "ChangeProcessor":
        from .core.change_processor import ChangeProcessor
        return ChangeProcessor
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
