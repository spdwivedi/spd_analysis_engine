# Web Server & REST API Subsystem (`scripts/server/`)

## Module Purpose
The `server` module provides the standard-library HTTP server, declarative REST API contract dispatcher, Server-Sent Events (SSE) notification stream, and local security hardening for the SPD Analysis Engine. It handles read queries via concurrent read-only database connections and dispatches lifecycle commands to the authoritative controller.

---

## File Roster & Technical Reference

- **`__init__.py`**
  - Package initialization and exports for web server classes.

- **`api_contract.py`**
  - **Classes:** `RouteContract`
  - **Core Functions:** `_r(...)`, `_compile_route_pattern(path)`, `match_route(method, path)`, `get_api_contract_catalog()`
  - **Purpose:** Centralized declarative API contract defining all route patterns, query/body schemas, read/mutation classifications, and authentication requirements.

- **`batch_runner.py`**
  - **Core Functions:** `run_async_batch(batch_fn, *args, **kwargs)`
  - **Purpose:** Asynchronous task runner for non-blocking batch operations.

- **`db_queries.py`**
  - **Core Functions:** `_open_ro_db(db_path)`, `_count_events_in_db(db_path)`, `_read_sessions_from_db(db_path)`, `_query_events(...)`, `_read_events_from_db(...)`, `resolve_burst_event(...)`
  - **Purpose:** High-performance read-only database query helpers opening SQLite connections in URI read-only mode (`?mode=ro`) to prevent write contention and lock contention.

- **`request_handler.py`**
  - **Classes:** `_EngineRequestHandler`
  - **Purpose:** HTTP request dispatcher inheriting from `http.server.BaseHTTPRequestHandler` and route mixins. Matches incoming requests against `API_CONTRACT`, validates tokens, and dispatches JSON responses.

- **`routes_ai.py`**
  - **Classes:** `AIRoutesMixin`
  - **Purpose:** Route handlers for basic AI synthesis endpoints and provider status checks.

- **`routes_ai_analysis.py`**
  - **Classes:** `AIAnalysisRoutesMixin`
  - **Purpose:** Route handlers for progressive level analysis (L1–L4) queries and on-demand burst synthesis.

- **`routes_ai_batch.py`**
  - **Classes:** `AIBatchRoutesMixin`
  - **Purpose:** Route handlers for asynchronous bulk AI analysis across historical sessions.

- **`routes_ai_jobs.py`**
  - **Classes:** `AIJobsRoutesMixin`
  - **Purpose:** Route handlers for controller-hosted durable AI job polling, retry dispatch, and status inspections.

- **`routes_events.py`**
  - **Classes:** `EventRoutesMixin`
  - **Purpose:** Primary routes handling timeline event querying and burst detail inspections.

- **`routes_events_api.py`**
  - **Classes:** `EventsApiRoutesMixin`
  - **Purpose:** Lightweight REST endpoints returning bounded event summaries (`GET /events`), event details (`GET /events/<id>`), and lazy on-demand diffs (`GET /events/<id>/files/<path>/diff`).

- **`routes_events_export.py`**
  - **Classes:** `EventExportRoutesMixin`
  - **Purpose:** Endpoints for exporting session histories as JSON or markdown summaries.

- **`routes_health.py`**
  - **Classes:** `HealthRoutesMixin`
  - **Purpose:** Real-time observability endpoint (`GET /health`) exposing queue depths, drop counters, writer latencies, and reconciler health.

- **`routes_projects.py`**
  - **Classes:** `ProjectRoutesMixin`
  - **Purpose:** Project lifecycle routes (`GET /api/projects`, `POST /api/project/start`, `POST /api/project/stop`). Strictly read-only for all GET queries.

- **`routes_projects_resume.py`**
  - **Classes:** `ProjectResumeRoutesMixin`
  - **Purpose:** Project session resumption and state recovery route handlers.

- **`routes_trash.py`**
  - **Classes:** `TrashRoutesMixin`
  - **Purpose:** Endpoints for inspecting soft-deleted project archives and executing selective purge operations.

- **`routes_workspace.py`**
  - **Classes:** `WorkspaceRoutesMixin`
  - **Purpose:** Workspace metadata inspection and configuration overrides.

- **`security.py`**
  - **Classes:** `SecurityHandlerMixin`
  - **Core Functions:** `generate_api_token()`, `is_valid_host(host_hdr)`, `is_trusted_origin(origin_hdr)`, `get_cors_headers(origin)`, `verify_auth_token(auth_hdr, expected_token)`, `sanitize_error_message(msg)`
  - **Purpose:** Local security enforcement. Validates Host headers against loopback, checks CORS origins, verifies ephemeral bearer tokens on mutations, and sanitizes error tracebacks.

- **`server_daemon.py`**
  - **Classes:** `_QuietThreadingHTTPServer`, `EngineWebServer`
  - **Purpose:** Socket daemon managing server lifecycle, static asset delivery from `web/`, background worker injection, and graceful shutdown.

- **`sse_stream.py`**
  - **Classes:** `SSEEvent`, `SSEClient`, `SSEManager`
  - **Core Functions:** `get_sse_manager()`, `set_sse_manager(mgr)`
  - **Purpose:** Server-Sent Events (SSE) notification bus (`/stream`, `/events/stream`, or `/api/events/stream`). Dispatches granular event notifications (`burst_sealed`, `status_changed`, `job_progress`) with bounded ring buffers (1,000 items) and `Last-Event-ID` reconnection replay.
