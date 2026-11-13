"""
scripts/server/__init__.py
==========================
Package containing modularized HTTP route mixins, server daemon, and database query utilities.
"""

from .routes_projects import ProjectRoutesMixin
from .routes_projects_resume import ProjectResumeRoutesMixin
from .routes_ai import AIRoutesMixin
from .routes_ai_analysis import AIAnalysisRoutesMixin
from .routes_ai_batch import AIBatchRoutesMixin
from .routes_trash import TrashRoutesMixin
from .routes_events import EventRoutesMixin
from .routes_events_export import EventExportRoutesMixin
from .routes_health import HealthRoutesMixin
from .routes_workspace import WorkspaceRoutesMixin
from .request_handler import _EngineRequestHandler
from .server_daemon import EngineWebServer, _QuietThreadingHTTPServer
from .batch_runner import run_async_batch
from .db_queries import _count_events_in_db, _read_sessions_from_db, _read_events_from_db

from .routes_events_api import EventsApiRoutesMixin
from .api_contract import API_CONTRACT, match_route, get_api_contract_catalog, RouteContract
from .sse_stream import SSEManager, get_sse_manager, set_sse_manager, SSEEvent, SSEClient

__all__ = [
    "ProjectRoutesMixin",
    "ProjectResumeRoutesMixin",
    "AIRoutesMixin",
    "AIAnalysisRoutesMixin",
    "AIBatchRoutesMixin",
    "TrashRoutesMixin",
    "EventRoutesMixin",
    "EventsApiRoutesMixin",
    "EventExportRoutesMixin",
    "HealthRoutesMixin",
    "WorkspaceRoutesMixin",
    "_EngineRequestHandler",
    "EngineWebServer",
    "_QuietThreadingHTTPServer",
    "run_async_batch",
    "_count_events_in_db",
    "_read_sessions_from_db",
    "_read_events_from_db",
    "API_CONTRACT",
    "match_route",
    "get_api_contract_catalog",
    "RouteContract",
    "SSEManager",
    "get_sse_manager",
    "set_sse_manager",
    "SSEEvent",
    "SSEClient",
]

