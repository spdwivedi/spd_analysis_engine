"""
scripts/server/request_handler.py
=================================
HTTP request dispatcher and SSE streaming handler for the SPD Analysis Engine.
Handles core endpoints including '/api/project/start' (alias for '/api/projects/start')
and '/api/events/stream' (SSE telemetry stream).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import queue
import re
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .routes_projects import ProjectRoutesMixin
from .routes_events import EventRoutesMixin
from .routes_events_api import EventsApiRoutesMixin
from .routes_ai import AIRoutesMixin
from .routes_ai_jobs import AIJobsRoutesMixin
from .routes_trash import TrashRoutesMixin
from .routes_health import HealthRoutesMixin
from .routes_workspace import WorkspaceRoutesMixin
from .security import SecurityHandlerMixin, is_trusted_origin, extract_auth_token, verify_auth_token
from .api_contract import match_route, get_api_contract_catalog, RouteContract
from .sse_stream import get_sse_manager, DEFAULT_HEARTBEAT_INTERVAL

logger = logging.getLogger(__name__)

try:
    from spd_analysis_engine.scripts.core.time_utils import utc_now_iso as _utcnow_iso
except (ImportError, ModuleNotFoundError):
    def _utcnow_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _EngineRequestHandler(
    SecurityHandlerMixin,
    ProjectRoutesMixin,
    EventRoutesMixin,
    EventsApiRoutesMixin,
    AIRoutesMixin,
    AIJobsRoutesMixin,
    TrashRoutesMixin,
    HealthRoutesMixin,
    WorkspaceRoutesMixin,
    BaseHTTPRequestHandler,
):
    """Custom request handler with REST API and SSE support."""

    def log_message(self, format: str, *args: Any) -> None:
        if args and str(args[0]).startswith("GET /api/stream"):
            return
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _handle_api_auth_token(self) -> None:
        port = getattr(self.server_instance, "port", 0)
        host = getattr(self.server_instance, "host", "127.0.0.1")
        origin = self.headers.get("Origin")

        # 1. If Origin header is present, validate against trusted loopback origin
        if origin:
            if not is_trusted_origin(origin, port, host):
                self._send_error("Forbidden: untrusted origin", status=403)
                return
            self._send_json({"token": getattr(self.server_instance, "api_token", ""), "status": "ok"})
            return

        # 2. If no Origin header, require existing valid API token or session cookie
        if getattr(self.server_instance, "require_token", True):
            token = extract_auth_token(self.headers)
            expected = getattr(self.server_instance, "api_token", "")
            if not token or not verify_auth_token(token, expected):
                self._send_error("Unauthorized: missing or invalid API token", status=401)
                return

        self._send_json({"token": getattr(self.server_instance, "api_token", ""), "status": "ok"})

    def _handle_api_contract(self) -> None:
        self._send_json({"contract": get_api_contract_catalog(), "status": "ok"})

    def _handle_api_rollback_burst_routed(self, name: str, item_id: str) -> None:
        try:
            eid = int(item_id)
        except ValueError:
            self._send_error("Invalid event_id; must be an integer", status=400)
            return
        self._handle_api_rollback_burst(name, eid)

    def _handle_api_synthesize_milestone_routed(
        self, name: str, body: dict[str, Any], query: dict[str, list[str]]
    ) -> None:
        if query.get("force_ai", [""])[0].lower() in ("true", "1", "yes"):
            body["force_ai"] = True
            body["mode"] = "tier2_ai"
        self._handle_api_synthesize_milestone(name, body)

    def _dispatch_route(
        self,
        route: RouteContract,
        params: dict[str, str],
        query: dict[str, list[str]],
        body: dict[str, Any] | None,
    ) -> None:
        handler = getattr(self, route.handler_name, None)
        if not handler:
            self._send_error(f"Handler {route.handler_name} not implemented", status=501)
            return

        name = params.get("name")
        item_id = params.get("item_id")
        event_id = params.get("id")
        file_path = params.get("file_path")

        if route.handler_name == "_handle_api_stream":
            self._handle_api_stream()
        elif route.handler_name == "_handle_api_events_summary":
            self._handle_api_events_summary(query)
        elif route.handler_name == "_handle_api_event_detail":
            self._handle_api_event_detail(event_id or "", query)
        elif route.handler_name == "_handle_api_event_file_diff":
            self._handle_api_event_file_diff(event_id or "", file_path or "", query)
        elif route.handler_name == "_handle_api_contract":
            self._handle_api_contract()
        elif route.handler_name == "_handle_api_auth_token":
            self._handle_api_auth_token()
        elif route.handler_name == "_handle_api_ai_job_enqueue":
            self._handle_api_ai_job_enqueue(body or {})
        elif route.handler_name == "_handle_api_ai_job_get":
            self._handle_api_ai_job_get(event_id or "")
        elif route.handler_name == "_handle_api_ai_job_retry":
            self._handle_api_ai_job_retry(event_id or "")
        elif route.handler_name == "_handle_api_ai_analysis_get":
            self._handle_api_ai_analysis_get(event_id or "")
        elif route.handler_name == "_handle_api_ai_target_analyses_get":
            self._handle_api_ai_target_analyses_get(params.get("kind", ""), params.get("target_id", ""))
        elif route.handler_name == "_handle_api_rollback_burst_routed":
            self._handle_api_rollback_burst_routed(name or "", item_id or "")
        elif route.handler_name == "_handle_api_synthesize_milestone_routed":
            self._handle_api_synthesize_milestone_routed(name or "", body or {}, query)
        elif route.handler_name in ("_handle_api_analyze_event", "_handle_api_analyze_exec"):
            handler(name, item_id, body or {})
        elif route.handler_name in ("_handle_api_restore_trash", "_handle_api_purge_trash"):
            handler(item_id)
        elif route.handler_name in ("_handle_api_get_file_analysis", "_handle_api_get_burst_overview", "_handle_api_worker_log_raw"):
            handler(name, query)
        elif route.handler_name in (
            "_handle_api_prepare_sync",
            "_handle_api_analyze_batch",
            "_handle_api_analyze_file",
            "_handle_api_analyze_burst_overview",
            "_handle_api_resume_project",
            "_handle_api_record_exec",
            "_handle_api_git_push",
            "_handle_api_delete_project",
            "_handle_api_put_gitignore",
        ):
            handler(name, body or {})
        elif route.handler_name in (
            "_handle_api_start_project",
            "_handle_api_stop_project",
            "_handle_api_save_git_config",
            "_handle_api_test_git_connection",
            "_handle_api_create_git_repo",
            "_handle_api_save_ai_config",
            "_handle_api_test_ai_connection",
            "_handle_api_internal_live_action",
        ):
            handler(body or {})
        elif name is not None:
            handler(name)
        else:
            handler()

    # GET Handlers
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        route, params = match_route("GET", path)
        if route:
            is_mutation_auth = route.auth_required
            ok, _ = self._validate_request(is_mutation=is_mutation_auth, requires_json=False, auth_required=route.auth_required)
            if not ok:
                return
            query = urllib.parse.parse_qs(parsed.query)
            self._dispatch_route(route, params, query, body=None)
            return

        self._handle_static_file(parsed.path)

    # POST Handlers
    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        route, params = match_route("POST", path)
        if not route:
            self._send_error(f"Endpoint not found: {path}", status=404)
            return

        ok, body = self._validate_request(is_mutation=True, requires_json=True, auth_required=route.auth_required)
        if not ok:
            return

        query = urllib.parse.parse_qs(parsed.query)
        self._dispatch_route(route, params, query, body)

    # PUT Handlers
    def do_PUT(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        route, params = match_route("PUT", path)
        if not route:
            self._send_error(f"Endpoint not found: {path}", status=404)
            return

        ok, body = self._validate_request(is_mutation=True, requires_json=True, auth_required=route.auth_required)
        if not ok:
            return

        query = urllib.parse.parse_qs(parsed.query)
        self._dispatch_route(route, params, query, body)

    # DELETE Handlers
    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        route, params = match_route("DELETE", path)
        if not route:
            self._send_error(f"Endpoint not found: {path}", status=404)
            return

        ok, _ = self._validate_request(is_mutation=True, requires_json=False, auth_required=route.auth_required)
        if not ok:
            return

        query = urllib.parse.parse_qs(parsed.query)
        self._dispatch_route(route, params, query, body=None)

    # SSE Stream & Broadcast
    @classmethod
    def broadcast_sse(cls, event: str, data: dict[str, Any]) -> None:
        """Broadcast an SSE event frame to all active web listeners via SSEManager."""
        get_sse_manager().broadcast(event, data)

    def _handle_api_stream(self) -> None:
        """Server-Sent Events (SSE) streaming endpoint."""
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        sse_mgr = get_sse_manager()
        last_event_id: int | None = None
        last_id_hdr = self.headers.get("Last-Event-ID")
        if last_id_hdr and str(last_id_hdr).strip().isdigit():
            last_event_id = int(str(last_id_hdr).strip())
        elif "last_event_id" in query and query["last_event_id"][0].isdigit():
            last_event_id = int(query["last_event_id"][0])
        elif "last_id" in query and query["last_id"][0].isdigit():
            last_event_id = int(query["last_id"][0])

        client, err = sse_mgr.register_client(last_event_id=last_event_id)
        if not client:
            self._send_error(err or "Too Many Requests", status=429)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._send_cors_headers()
        self.end_headers()

        try:
            init_frame = f"event: connected\ndata: {json.dumps({'time': _utcnow_iso(), 'client_id': client.client_id})}\n\n"
            self.wfile.write(init_frame.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            sse_mgr.unregister_client(client.client_id)
            return

        engine = self.server_instance
        try:
            while not engine._shutdown_event.is_set():
                try:
                    ev = client.q.get(timeout=DEFAULT_HEARTBEAT_INTERVAL)
                    self.wfile.write(ev.to_wire())
                    self.wfile.flush()
                except queue.Empty:
                    ping_payload = json.dumps({"time": _utcnow_iso(), "status": "ok"})
                    self.wfile.write(f"event: ping\ndata: {ping_payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        except Exception as exc:
            logger.debug("Error streaming SSE data: %s", exc)
        finally:
            sse_mgr.unregister_client(client.client_id)

    # Static Assets Serving
    def _handle_static_file(self, raw_path: str) -> None:
        engine = self.server_instance
        clean_path = raw_path.lstrip("/")
        if not clean_path or clean_path == "/":
            clean_path = "index.html"

        file_path = (engine.web_dir / clean_path).resolve()
        try:
            file_path.relative_to(engine.web_dir)
        except ValueError:
            self._send_error("Forbidden", status=403)
            return

        if not file_path.is_file():
            self._send_error(f"File not found: {clean_path}", status=404)
            return

        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type is None:
            mime_type = "application/octet-stream"
        if mime_type.startswith("text/") or mime_type in ("application/javascript", "application/json"):
            mime_type += "; charset=utf-8"

        try:
            content = file_path.read_bytes()
            extra_headers: dict[str, str] = {}
            if clean_path in ("index.html", "") or file_path.name == "index.html":
                token = getattr(engine, "api_token", "")
                if token:
                    extra_headers["Set-Cookie"] = f"spd_token={token}; Path=/; SameSite=Strict"
                    html_text = content.decode("utf-8", errors="replace")
                    token_tag = f'\n  <meta name="spd-api-token" content="{token}">\n  <script>window.__SPD_API_TOKEN__="{token}";</script>'
                    if "</head>" in html_text:
                        html_text = html_text.replace("</head>", f"{token_tag}\n</head>", 1)
                    else:
                        html_text = f"{token_tag}\n{html_text}"
                    content = html_text.encode("utf-8")

            if clean_path == "style.css" or file_path.name == "style.css":
                text = content.decode("utf-8", errors="replace")
                if "@import" in text:
                    def _replace_import(match: re.Match) -> str:
                        import_path = (match.group(1) or match.group(2) or "").strip("'\"")
                        target_file = (file_path.parent / import_path).resolve()
                        if target_file.is_file():
                            return target_file.read_text(encoding="utf-8", errors="replace")
                        return match.group(0)

                    bundled = re.sub(
                        r"""@import\s+(?:url\(['"]?([^'"]+)['"]?\)|['"]([^'"]+)['"]);?""",
                        _replace_import,
                        text,
                    )
                    content = bundled.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self._send_cors_headers()
            for k, v in extra_headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(content)
        except Exception as exc:
            self._send_error(f"Error reading file: {exc}", status=500)
