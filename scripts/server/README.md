# scripts/server/ — HTTP Server & Route Mixins

This sub-package implements the zero-dependency native Python HTTP server, the SSE event broadcaster, and all REST API route handlers organized as composable mixins.

---

## Module Map

| Module | Responsibility |
|---|---|
| [`server_daemon.py`](./server_daemon.py) | `EngineWebServer`, `_QuietThreadingHTTPServer` — server lifecycle |
| [`request_handler.py`](./request_handler.py) | `_EngineRequestHandler` — composed mixin handler class |
| [`routes_projects.py`](./routes_projects.py) | `ProjectRoutesMixin` — project start/stop/resume/status |
| [`routes_projects_resume.py`](./routes_projects_resume.py) | `ProjectResumeRoutesMixin` — extracted resume logic |
| [`routes_events.py`](./routes_events.py) | `EventRoutesMixin` — events, sessions, logs, git ops |
| [`routes_events_export.py`](./routes_events_export.py) | `EventExportRoutesMixin` — export and advanced git routes |
| [`routes_ai.py`](./routes_ai.py) | `AIRoutesMixin` — AI config + event/exec analysis |
| [`routes_ai_analysis.py`](./routes_ai_analysis.py) | `AIAnalysisRoutesMixin` — batch, file, and burst overview analysis |
| [`routes_ai_batch.py`](./routes_ai_batch.py) | `AIBatchRoutesMixin` — async batch analysis job management |
| [`routes_trash.py`](./routes_trash.py) | `TrashRoutesMixin` — trash list, restore, purge |
| [`batch_runner.py`](./batch_runner.py) | `run_async_batch()` — background batch analysis worker |
| [`db_queries.py`](./db_queries.py) | `resolve_burst_event()`, cached DB read helpers |

---

## Server Architecture

### `EngineWebServer` (`server_daemon.py`)

```python
server = EngineWebServer(port=8765, engine_root=Path("."))
server.start()  # Blocks; starts ThreadingHTTPServer
```

`EngineWebServer` wraps `_QuietThreadingHTTPServer` (a subclass of `http.server.ThreadingHTTPServer`) with suppressed server logging for cleaner output.

The server holds:
- `self.supervisor` — reference to the `Supervisor` instance
- `self._projects` — live project registry dict
- `self._batch_jobs` — batch analysis job state dict
- `self._batch_jobs_lock` — threading lock for batch state
- `self._sse_clients` — list of open SSE response objects

### `_EngineRequestHandler` (`request_handler.py`)

The composed request handler uses Python's multiple-inheritance mixin pattern:

```python
class _EngineRequestHandler(
    ProjectRoutesMixin,
    ProjectResumeRoutesMixin,
    EventRoutesMixin,
    EventExportRoutesMixin,
    AIRoutesMixin,
    AIAnalysisRoutesMixin,
    AIBatchRoutesMixin,
    TrashRoutesMixin,
    BaseHTTPRequestHandler,
):
    """
    Composed HTTP request handler.
    MRO resolves all _handle_api_* methods from the mixins above.
    BaseHTTPRequestHandler provides do_GET / do_POST plumbing.
    """
```

**Method Resolution Order (MRO)** ensures that each `_handle_api_*` method is resolved from the most specific mixin. No method is duplicated.

---

## Route Mixin Pattern

Each mixin class provides a set of `_handle_api_*` methods. They never define `__init__` or `do_GET/do_POST` — those come from `BaseHTTPRequestHandler`.

### Shared Helper Methods (on `ProjectRoutesMixin`)

All mixins access these via MRO:

```python
def _resolve_target_path(self, slug: str) -> Path | None:
    """Find the workspace target_path for a project slug across all tiers."""

def _resolve_db_path(self, slug: str) -> Path | None:
    """Find the session.db path for a project slug across current/last_run/history."""
```

### Response Helpers (on `BaseHTTPRequestHandler` via `_EngineRequestHandler`)

```python
def _send_json(self, data: dict, status: int = 200) -> None:
    """Send JSON response with correct Content-Type header."""

def _send_error(self, message: str, status: int = 400) -> None:
    """Send JSON error response."""
```

---

## SSE Event Stream

The SSE stream endpoint is handled by `EventRoutesMixin._handle_api_events_stream()`.

### Connection Lifecycle

```python
def _handle_api_events_stream(self):
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream")
    self.send_header("Cache-Control", "no-cache")
    self.send_header("Connection", "keep-alive")
    self.end_headers()

    # Register this client
    self.server_instance._sse_clients.append(self.wfile)

    try:
        # Keep the connection alive until client disconnects
        while True:
            time.sleep(5)
            self.wfile.write(b"data: {\"event_type\": \"heartbeat\"}\n\n")
            self.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        pass  # Client disconnected
    finally:
        self.server_instance._sse_clients.remove(self.wfile)
```

### Broadcasting Events

```python
def _broadcast_sse(self, event: dict) -> None:
    payload = f"data: {json.dumps(event)}\n\n".encode("utf-8")
    dead_clients = []
    for client in self.server_instance._sse_clients:
        try:
            client.write(payload)
            client.flush()
        except Exception:
            dead_clients.append(client)
    for c in dead_clients:
        self.server_instance._sse_clients.remove(c)
```

---

## Route Reference

### Project Routes (`ProjectRoutesMixin`)

| Endpoint | Method | Handler |
|---|---|---|
| `/api/status` | GET | `_handle_api_status` |
| `/api/projects` | GET | `_handle_api_projects` |
| `/api/project/start` | POST | `_handle_api_start_project` |
| `/api/project/<n>/stop` | POST | `_handle_api_stop_project` |
| `/api/project/<n>/resume` | POST | `_handle_api_resume_project` |
| `/api/project/<n>/specs` | GET | `_handle_api_project_specs` |

### Event & Data Routes (`EventRoutesMixin`)

| Endpoint | Method | Handler |
|---|---|---|
| `/api/project/<n>/events` | GET | `_handle_api_events` |
| `/api/project/<n>/sessions` | GET | `_handle_api_sessions` |
| `/api/project/<n>/logs` | GET | `_handle_api_logs` |
| `/api/project/<n>/export` | GET | `_handle_api_export` |
| `/api/project/<n>/seal-burst` | POST | `_handle_api_seal_burst` |
| `/api/project/<n>/rollback-burst` | POST | `_handle_api_rollback_burst` |
| `/api/events/stream` | GET | `_handle_api_events_stream` |

### AI Routes (`AIRoutesMixin` + `AIAnalysisRoutesMixin`)

| Endpoint | Method | Handler |
|---|---|---|
| `/api/ai/config` | GET | `_handle_api_get_ai_config` |
| `/api/ai/config` | POST | `_handle_api_save_ai_config` |
| `/api/ai/test` | POST | `_handle_api_test_ai_connection` |
| `/api/project/<n>/analyze-event/<id>` | POST | `_handle_api_analyze_event` |
| `/api/project/<n>/analyze-exec/<id>` | POST | `_handle_api_analyze_exec` |
| `/api/project/<n>/analyze-batch` | POST | `_handle_api_analyze_batch` |
| `/api/project/<n>/analyze-batch/status` | GET | `_handle_api_analyze_batch_status` |
| `/api/project/<n>/analyze-file` | POST | `_handle_api_analyze_file` |
| `/api/project/<n>/file-analysis` | GET | `_handle_api_get_file_analysis` |
| `/api/project/<n>/burst-overview` | GET | `_handle_api_get_burst_overview` |
| `/api/project/<n>/analyze-burst-overview` | POST | `_handle_api_analyze_burst_overview` |

### Git Routes (`EventRoutesMixin`)

| Endpoint | Method | Handler |
|---|---|---|
| `/api/git/config` | GET | `_handle_api_get_git_config` |
| `/api/git/config` | POST | `_handle_api_save_git_config` |
| `/api/git/test` | POST | `_handle_api_test_git_connection` |
| `/api/git/create-repo` | POST | `_handle_api_create_git_repo` |
| `/api/git/push` | POST | `_handle_api_git_push` |

---

## Async Batch Analysis (`batch_runner.py`)

Background batch AI analysis runs in a `threading.Thread` (not a separate process) to share memory with the HTTP server for status updates.

```python
def run_async_batch(
    slug: str,
    job_id: str,
    event_ids: list[int],
    db_path: Path,
    target_path: Path,
    engine: EngineWebServer,
) -> None:
    """
    Analyzes each event_id sequentially using AISynthesizer.
    Updates engine._batch_jobs[slug] with progress after each event.
    Broadcasts ai_analysis_ready SSE on each completion.
    Respects rate_limiter cooldowns between calls.
    """
```

The batch runner updates `engine._batch_jobs[slug]` with:
- `analyzed_count`: how many have completed
- `current_event_id`: which event is being processed right now
- `status`: `"running"` | `"completed"` | `"failed"`

---

## `db_queries.py` — Database Helper Functions

### `resolve_burst_event(db_path, session_id, burst_id, event_id, burst_number)`

The central query resolver. Takes any combination of `session_id`, `burst_id`, `event_id`, or `burst_number` and returns the canonical `(event_id, burst_number, session_id)` triple.

This is critical because the frontend may reference bursts by different IDs depending on the view (timeline uses `event_id`, the batch status uses `burst_number`).

```python
resolved_eid, resolved_bnum, resolved_sid = resolve_burst_event(
    db_path=Path("current/nova_pulse/session.db"),
    session_id=None,
    burst_id=None,
    event_id=None,
    burst_number=5,
)
# Returns: (42, 5, 1) — event_id=42, burst_number=5, session_id=1
```
