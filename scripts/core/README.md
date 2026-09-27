# scripts/core/ — Supervisor & Process Lifecycle

This sub-package manages the entire lifecycle of project worker processes: spawning, health monitoring, crash detection, and graceful shutdown.

---

## Module Map

| Module | Responsibility |
|---|---|
| [`supervisor.py`](./supervisor.py) | Central orchestrator — project start/stop/resume, worker registry |
| [`spawner.py`](./spawner.py) | OS-level process creation with detached arguments |
| [`reaper.py`](./reaper.py) | Dead worker detection and force-kill |
| [`worker_bundle.py`](./worker_bundle.py) | Worker initialization bundle (DB, Shadow Git, watchers) |
| [`worker_entry.py`](./worker_entry.py) | Worker process main loop entrypoint |
| [`worker_helpers.py`](./worker_helpers.py) | Helper utilities extracted from supervisor |
| [`worker_lifecycle.py`](./worker_lifecycle.py) | Worker startup sequence and shutdown hooks |

---

## Supervisor (`supervisor.py`)

The `Supervisor` is a singleton that the HTTP server holds a reference to. It manages:

### Project Registry

```python
# Internal state (protected by threading.Lock)
self._projects: dict[str, dict] = {}
# slug → {
#   "status": "running" | "stopped" | "crashed",
#   "pid": int,
#   "target_path": str,
#   "debounce_window": float,
#   "started_at": str,
# }
```

### `start_project(slug, target_path, debounce_window, ...)` 

1. Calls `StorageRotator.init_project(slug)` to create `current/<slug>/`.
2. Writes initial `project.meta` with `debounce_window`, `target_path`, and `powers`.
3. Calls `Spawner.spawn_worker(slug)` to fork the worker process.
4. Records `worker_pid` in `project.meta`.
5. Adds the project to `_projects` registry.

### `stop_project(slug)`

1. Sends `SIGTERM` to the worker PID.
2. Waits up to 5 seconds for graceful shutdown.
3. If still alive, sends `SIGKILL`.
4. Calls `StorageRotator.archive_project(slug)` to move `current/` → `last_run/`.
5. Updates registry status to `"stopped"`.

### `resume_project(slug, ...)`

1. Reads `project.meta` from `last_run/<slug>/`.
2. Auto-hydrates `debounce_window`, `target_path`, and `powers` from saved meta.
3. Moves `last_run/<slug>/` back to `current/<slug>/`.
4. Spawns a new worker.

### Heartbeat Grace Threshold

```python
hb_grace_threshold = max(debounce_window, 12.0)
```

This ensures that a large debounce window (e.g. 600s) doesn't cause the Supervisor to incorrectly flag a quiet editing session as a crashed worker.

---

## Spawner (`spawner.py`)

The Spawner creates OS-level detached processes for project workers.

### Windows

```python
# Uses CREATE_NO_WINDOW + DETACHED_PROCESS flags
subprocess.Popen(
    [sys.executable, "scripts/project_worker.py", slug],
    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    close_fds=True,
)
```

### Linux / macOS

```python
subprocess.Popen(
    [sys.executable, "scripts/project_worker.py", slug],
    start_new_session=True,  # Detaches from parent process group
    close_fds=True,
)
```

The `close_fds=True` ensures the worker does not inherit the parent's open file descriptors (including the HTTP server socket).

---

## Reaper (`reaper.py`)

The Reaper runs as a background thread inside the Supervisor. Every 10 seconds it:

1. Iterates all projects with status `"running"`.
2. Reads `last_heartbeat_at` from `project.meta`.
3. If `now - last_heartbeat_at > hb_grace_threshold`:
   - Marks the project as `"crashed"` in the registry.
   - Attempts `SIGTERM` then `SIGKILL`.
   - Logs the crash to the session DB.
   - Broadcasts a `project_crashed` SSE event to connected dashboards.

### OS PID Validation

Before acting on a stale heartbeat, the Reaper validates the PID:

```python
import psutil
try:
    proc = psutil.Process(pid)
    if proc.is_running() and "project_worker" in proc.cmdline():
        continue  # Worker is alive, not crashed
except psutil.NoSuchProcess:
    pass  # PID is dead — proceed with crash handling
```

---

## Worker Bundle (`worker_bundle.py`)

`WorkerBundle` is an initialization container that a project worker receives on startup. It holds:

```python
@dataclass
class WorkerBundle:
    slug: str
    target_path: Path
    engine_root: Path
    session_db: SessionDB
    shadow_git: ShadowGit
    event_bundler: EventBundler
    watcher_fs: WatcherFS
    debounce_window: float
    powers: dict[str, bool]
```

The bundle pattern ensures that all resources (DB connections, shadow git handle, watcher) are initialized once at worker startup and reused for the lifetime of the session.

---

## Worker Lifecycle (`worker_lifecycle.py`)

Manages the startup sequence and shutdown hooks for each worker:

### Startup Sequence

```
1. Read project.meta
2. Open SessionDB (WAL mode)
3. Init ShadowGit (create shadow_git/ if not exists)
4. Start WatcherFS
5. Start EventBundler thread
6. Write "running" status to project.meta
7. Start heartbeat updater thread (every 5s)
8. Enter main event loop
```

### Shutdown Hooks

On `SIGTERM`:
1. Stop `WatcherFS`
2. Flush `EventBundler` (seal any open burst)
3. Write final heartbeat
4. Close `SessionDB` (WAL checkpoint)
5. Exit cleanly

On unexpected crash:
- Python's `atexit` handler writes `{"status": "crashed"}` to `project.meta`
- This allows the Supervisor/Reaper to detect the crash on next poll
