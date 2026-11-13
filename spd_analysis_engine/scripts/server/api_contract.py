"""
scripts/server/api_contract.py
==============================
Authoritative API contract table, route definitions, parameter extractors,
and alias registration for the SPD Analysis Engine HTTP interface.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RouteContract:
    """Canonical definition of an HTTP route in the SPD Analysis Engine."""

    method: str
    path: str
    handler_name: str
    auth_required: bool = False
    is_mutation: bool = False
    request_schema: dict[str, Any] = field(default_factory=dict)
    response_schema: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Serialize route contract metadata for discovery and inspection."""
        return {
            "method": self.method,
            "path": self.path,
            "handler_name": self.handler_name,
            "auth_required": self.auth_required,
            "is_mutation": self.is_mutation,
            "classification": "mutation" if self.is_mutation else "read",
            "request_schema": self.request_schema,
            "response_schema": self.response_schema,
            "description": self.description,
            "aliases": list(self.aliases),
        }


# -----------------------------------------------------------------------------
# Canonical API Contract Table
# -----------------------------------------------------------------------------

def _r(
    method: str,
    path: str,
    handler_name: str,
    *,
    auth: bool = False,
    mutation: bool = False,
    req_schema: dict[str, Any] | None = None,
    resp_schema: dict[str, Any] | None = None,
    desc: str = "",
    aliases: tuple[str, ...] = (),
) -> RouteContract:
    return RouteContract(
        method=method,
        path=path,
        handler_name=handler_name,
        auth_required=auth,
        is_mutation=mutation,
        request_schema=req_schema or {},
        response_schema=resp_schema or {},
        description=desc,
        aliases=aliases,
    )


API_CONTRACT: list[RouteContract] = [
    # Core Events API (Canonical)
    _r("GET", "/events", "_handle_api_events_summary",
       req_schema={"query": {"since_id": "int?", "limit": "int?", "types": "str?", "session_id": "int?", "project": "str?"}},
       resp_schema={"events": "list[EventSummary]", "total": "int", "has_more": "bool"},
       desc="Fetch bounded event summaries without full diff bodies.",
       aliases=("/api/events",)),
    _r("GET", "/events/{id}", "_handle_api_event_detail",
       req_schema={"path": {"id": "int|str"}},
       resp_schema={"event": "dict", "files": "list[FileSummary]"},
       desc="Fetch single event metadata and touched files without diffs.",
       aliases=("/api/events/{id}",)),
    _r("GET", "/events/{id}/files/{file_path:path}/diff", "_handle_api_event_file_diff",
       req_schema={"path": {"id": "int|str", "file_path": "str"}},
       resp_schema={"event_id": "int|str", "file_path": "str", "diff_content": "str", "additions": "int", "deletions": "int"},
       desc="Fetch diff on demand for single file in event.",
       aliases=("/api/events/{id}/files/{file_path:path}/diff",
                "/api/projects/{name}/events/{id}/files/{file_path:path}/diff",
                "/api/project/{name}/events/{id}/files/{file_path:path}/diff")),

    # Legacy Event & Session Endpoints
    _r("GET", "/api/projects/{name}/events", "_handle_api_events",
       req_schema={"query": {"limit": "int?"}},
       resp_schema={"project": "str", "events": "list", "sessions": "list"},
       desc="Legacy event endpoint with bounded results.",
       aliases=("/api/project/{name}/events",)),
    _r("GET", "/api/projects/{name}/sessions", "_handle_api_sessions",
       desc="Fetch all sessions and burst metrics.",
       aliases=("/api/project/{name}/sessions",)),

    # System, SSE, and Status Endpoints
    _r("GET", "/api/status", "_handle_api_status", desc="Get supervisor status.", aliases=("/status",)),
    _r("GET", "/api/projects", "_handle_api_projects", desc="List all tracked projects.", aliases=("/projects",)),
    _r("GET", "/api/stream", "_handle_api_stream", auth=True, desc="SSE realtime event stream.", aliases=("/stream",)),
    _r("GET", "/api/auth/token", "_handle_api_auth_token", desc="API token discovery."),
    _r("GET", "/api/contract", "_handle_api_contract", desc="Catalog of all API contract endpoints."),
    _r("GET", "/api/engine/health", "_handle_api_engine_health", desc="System health and telemetry.", aliases=("/api/health", "/health")),

    # Project Read Endpoints
    _r("GET", "/api/projects/{name}/logs", "_handle_api_logs", aliases=("/api/project/{name}/logs",)),
    _r("GET", "/api/projects/{name}/export", "_handle_api_export", aliases=("/api/project/{name}/export",)),
    _r("GET", "/api/projects/{name}/shadow-git", "_handle_api_shadow_git", aliases=("/api/project/{name}/shadow-git",)),
    _r("GET", "/api/projects/{name}/specs", "_handle_api_project_specs", aliases=("/api/project/{name}/specs",)),
    _r("GET", "/api/projects/{name}/gitignore", "_handle_api_get_gitignore", aliases=("/api/project/{name}/gitignore",)),
    _r("GET", "/api/projects/{name}/milestones", "_handle_api_project_milestones", aliases=("/api/project/{name}/milestones",)),
    _r("GET", "/api/projects/{name}/unpushed-milestone", "_handle_api_unpushed_milestone", aliases=("/api/project/{name}/unpushed-milestone",)),
    _r("GET", "/api/projects/{name}/file-analysis", "_handle_api_get_file_analysis", aliases=("/api/project/{name}/file-analysis",)),
    _r("GET", "/api/projects/{name}/burst-overview", "_handle_api_get_burst_overview", aliases=("/api/project/{name}/burst-overview",)),
    _r("GET", "/api/projects/{name}/worker-log-raw", "_handle_api_worker_log_raw", aliases=("/api/project/{name}/worker-log-raw",)),
    _r("GET", "/api/projects/{name}/analyze-batch/status", "_handle_api_analyze_batch_status", aliases=("/api/project/{name}/analyze-batch/status",)),
    _r("GET", "/api/git/config", "_handle_api_get_git_config"),
    _r("GET", "/api/ai/config", "_handle_api_get_ai_config"),
    _r("GET", "/api/ai/jobs/{id}", "_handle_api_ai_job_get",
       req_schema={"path": {"id": "str"}},
       resp_schema={"success": "bool", "job": "dict"},
       desc="Fetch status of durable AI analysis job."),
    _r("GET", "/api/ai/analyses/{id}", "_handle_api_ai_analysis_get",
       req_schema={"path": {"id": "str"}},
       resp_schema={"success": "bool", "analysis": "dict"},
       desc="Fetch durable AI analysis result."),
    _r("GET", "/api/ai/analyses/target/{kind}/{target_id}", "_handle_api_ai_target_analyses_get",
       req_schema={"path": {"kind": "str", "target_id": "str"}},
       resp_schema={"success": "bool", "analyses": "list[dict]"},
       desc="Fetch all progressive analyses for target."),
    _r("GET", "/api/trash", "_handle_api_get_trash"),

    # Mutations (POST)
    _r("POST", "/api/projects/start", "_handle_api_start_project", auth=True, mutation=True),
    _r("POST", "/api/projects/stop", "_handle_api_stop_project", auth=True, mutation=True),
    _r("POST", "/api/git/config", "_handle_api_save_git_config", auth=True, mutation=True),
    _r("POST", "/api/git/test", "_handle_api_test_git_connection", auth=True, mutation=True),
    _r("POST", "/api/git/create-repo", "_handle_api_create_git_repo", auth=True, mutation=True),
    _r("POST", "/api/ai/config", "_handle_api_save_ai_config", auth=True, mutation=True),
    _r("POST", "/api/ai/test", "_handle_api_test_ai_connection", auth=True, mutation=True),
    _r("POST", "/api/ai/jobs/enqueue", "_handle_api_ai_job_enqueue", auth=False, mutation=True,
       req_schema={"body": {"project_slug": "str", "target_kind": "str", "target_id": "str", "diff_text": "str"}},
       resp_schema={"success": "bool", "job_id": "str", "status": "str"},
       desc="Enqueue an AI analysis job into controller queue."),
    _r("POST", "/api/ai/jobs/{id}/retry", "_handle_api_ai_job_retry", auth=False, mutation=True,
       req_schema={"path": {"id": "str"}},
       resp_schema={"success": "bool", "job_id": "str", "status": "str"},
       desc="Manual Retry action for failed AI analysis job."),
    _r("POST", "/api/projects/{name}/prepare-sync", "_handle_api_prepare_sync", auth=True, mutation=True, aliases=("/api/project/{name}/prepare-sync",)),
    _r("POST", "/api/projects/{name}/analyze-batch", "_handle_api_analyze_batch", auth=True, mutation=True, aliases=("/api/project/{name}/analyze-batch",)),
    _r("POST", "/api/projects/{name}/analyze-file", "_handle_api_analyze_file", auth=True, mutation=True, aliases=("/api/project/{name}/analyze-file",)),
    _r("POST", "/api/projects/{name}/analyze-burst-overview", "_handle_api_analyze_burst_overview", auth=True, mutation=True, aliases=("/api/project/{name}/analyze-burst-overview",)),
    _r("POST", "/api/projects/{name}/resume", "_handle_api_resume_project", auth=True, mutation=True, aliases=("/api/project/{name}/resume",)),
    _r("POST", "/api/projects/{name}/exec", "_handle_api_record_exec", auth=True, mutation=True, aliases=("/api/project/{name}/exec",)),
    _r("POST", "/api/projects/{name}/git-push", "_handle_api_git_push", auth=True, mutation=True, aliases=("/api/project/{name}/git-push",)),
    _r("POST", "/api/projects/{name}/delete", "_handle_api_delete_project", auth=True, mutation=True, aliases=("/api/project/{name}/delete",)),
    _r("POST", "/api/projects/{name}/gitignore", "_handle_api_put_gitignore", auth=True, mutation=True, aliases=("/api/project/{name}/gitignore",)),
    _r("POST", "/api/projects/{name}/synthesize-milestone", "_handle_api_synthesize_milestone_routed", auth=True, mutation=True, aliases=("/api/project/{name}/synthesize-milestone",)),
    _r("POST", "/api/projects/{name}/seal-burst", "_handle_api_seal_burst", auth=True, mutation=True, aliases=("/api/project/{name}/seal-burst",)),
    _r("POST", "/api/projects/{name}/analyze-event/{item_id}", "_handle_api_analyze_event", auth=True, mutation=True, aliases=("/api/project/{name}/analyze-event/{item_id}",)),
    _r("POST", "/api/projects/{name}/analyze-exec/{item_id}", "_handle_api_analyze_exec", auth=True, mutation=True, aliases=("/api/project/{name}/analyze-exec/{item_id}",)),
    _r("POST", "/api/projects/{name}/rollback-burst/{item_id}", "_handle_api_rollback_burst_routed", auth=True, mutation=True, aliases=("/api/project/{name}/rollback-burst/{item_id}",)),
    _r("POST", "/api/trash/restore/{item_id}", "_handle_api_restore_trash", auth=True, mutation=True),
    _r("POST", "/api/trash/purge/{item_id}", "_handle_api_purge_trash", auth=True, mutation=True),
    _r("POST", "/api/trash/purge-all", "_handle_api_purge_all_trash", auth=True, mutation=True),
    _r("POST", "/api/system/recovery/purge-zombies", "_handle_api_purge_zombies", auth=True, mutation=True),
    _r("POST", "/api/system/reconcile", "_handle_api_system_reconcile", auth=True, mutation=True),
    _r("POST", "/api/internal/live-action", "_handle_api_internal_live_action", auth=False, mutation=True,
       req_schema={"body": {"project": "str?", "event_type": "str", "path": "str", "timestamp": "str?"}},
       resp_schema={"status": "str"},
       desc="Internal loopback route for worker real-time live action ingestion."),

    # Mutations (PUT)
    _r("PUT", "/api/projects/{name}/gitignore", "_handle_api_put_gitignore", auth=True, mutation=True, aliases=("/api/project/{name}/gitignore",)),

    # Mutations (DELETE)
    _r("DELETE", "/api/trash/purge/{item_id}", "_handle_api_purge_trash", auth=True, mutation=True),
    _r("DELETE", "/api/trash/purge-all", "_handle_api_purge_all_trash", auth=True, mutation=True),
]


# -----------------------------------------------------------------------------
# Route Matching and Pattern Compilation
# -----------------------------------------------------------------------------

def _compile_route_pattern(route_pattern: str) -> re.Pattern:
    """Compile route path pattern into a regex supporting named capture groups."""
    pattern = re.escape(route_pattern)
    pattern = re.sub(r"\\\{([a-zA-Z_0-9]+):path\\\}", r"(?P<\1>.+)", pattern)
    pattern = re.sub(r"\\\{([a-zA-Z_0-9]+)\\\}", r"(?P<\1>[^/]+)", pattern)
    return re.compile(rf"^{pattern}/?$", re.IGNORECASE)


_COMPILED_ROUTES: dict[str, list[tuple[re.Pattern, RouteContract]]] = {}

for _rc in API_CONTRACT:
    method_routes = _COMPILED_ROUTES.setdefault(_rc.method.upper(), [])
    for _p in [_rc.path] + list(_rc.aliases):
        method_routes.append((_compile_route_pattern(_p), _rc))


def match_route(method: str, path: str) -> tuple[RouteContract | None, dict[str, str]]:
    """
    Match HTTP method and URL path against authoritative API contract.
    Returns (matching_contract, extracted_path_params).
    """
    routes = _COMPILED_ROUTES.get(method.upper(), [])
    clean_path = path.rstrip("/") or "/"

    for pattern, contract in routes:
        match = pattern.match(clean_path)
        if match:
            raw_params = match.groupdict()
            unquoted_params = {k: urllib.parse.unquote(v) for k, v in raw_params.items()}
            return contract, unquoted_params

    return None, {}


def get_api_contract_catalog() -> list[dict[str, Any]]:
    """Export complete API contract catalog for documentation and runtime introspection."""
    return [rc.to_dict() for rc in API_CONTRACT]
