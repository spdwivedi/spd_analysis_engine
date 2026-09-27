# scripts/ — Backend Python Package

This directory contains all backend logic for the SPD Analysis Engine, organized as a set of specialized sub-packages with a thin set of root-level orchestration modules.

---

## Root Module Responsibilities

| Module | Role |
|---|---|
| [`start.py`](../start.py) | CLI entrypoint — parses args, starts the HTTP server |
| [`project_worker.py`](./project_worker.py) | Per-project detached process entrypoint |
| [`orchestrator.py`](./orchestrator.py) | Multi-project lifecycle coordinator |
| [`watcher_fs.py`](./watcher_fs.py) | Cross-platform filesystem change watcher |
| [`event_bundler.py`](./event_bundler.py) | Debounce sliding-window burst grouper |
| [`diff_calc.py`](./diff_calc.py) | Unified diff calculation from file snapshots |
| [`shell_interceptor.py`](./shell_interceptor.py) | Shell command event capture & routing |
| [`ide_profiles.py`](./ide_profiles.py) | IDE-specific ignore pattern profiles |

### Facade Modules (backwards-compatible re-exports)

These files exist purely as backward-compatible facades. All real logic has been extracted into sub-packages.

| Facade | Delegates To |
|---|---|
| [`ai_analyzer.py`](./ai_analyzer.py) | `scripts/ai/` |
| [`git_shadow.py`](./git_shadow.py) | `scripts/git/` |
| [`storage_rotator.py`](./storage_rotator.py) | `scripts/storage/` |
| [`trash_manager.py`](./trash_manager.py) | `scripts/trash/` |
| [`web_server.py`](./web_server.py) | `scripts/server/` |

---

## Sub-Package Map

```
scripts/
├── core/           Supervisor daemon, spawner, reaper, worker lifecycle
├── storage/        SQLite WAL engine, project rotation, metadata
├── git/            Shadow Git micro-versioning, rollback, remote push
├── ai/             AI synthesis pipeline, rate limiter, cache, prompts
├── server/         HTTP server, SSE stream, REST route mixins
├── shell/          Shell command classification and AST parsing
└── trash/          Soft-delete system, Windows-safe file ops
```

---

## Entrypoint Architecture

### `start.py` → `EngineWebServer`

```
python start.py ui --port 8765
        │
        └── EngineWebServer(port=8765)
                │
                ├── Supervisor.start()           → manages project workers
                ├── _EngineRequestHandler        → handles every HTTP request
                │       ├── ProjectRoutesMixin   → /api/project/*
                │       ├── EventRoutesMixin     → /api/events, /api/sessions
                │       ├── AIRoutesMixin        → /api/ai/*, /api/analyze-*
                │       ├── TrashRoutesMixin     → /api/trash/*
                │       └── BaseHTTPRequestHandler (stdlib)
                └── SSEBroadcaster              → pushes to all /api/events/stream clients
```

### `project_worker.py` — Per-Project Worker

Each project runs in its own `multiprocessing.Process`. The worker:

1. Starts `WatcherFS` on the `target_path`
2. Routes raw filesystem events to `EventBundler`
3. On burst seal: writes to `SessionDB`, commits to `ShadowGit`, pushes SSE
4. Updates `project.meta` heartbeat timestamp every 5 seconds

### `orchestrator.py` — Multi-Project Coordinator

When the engine manages multiple projects simultaneously, `Orchestrator` maintains the registry of active workers and coordinates SSE broadcasting to ensure events from all projects are interleaved correctly in the dashboard stream.

---

## Dependency Graph (simplified)

```
start.py
  └── scripts/server/ (EngineWebServer)
        ├── scripts/core/ (Supervisor → spawns workers)
        │     └── project_worker.py
        │           ├── watcher_fs.py
        │           ├── event_bundler.py
        │           ├── diff_calc.py
        │           ├── scripts/storage/ (SessionDB)
        │           ├── scripts/git/ (ShadowGit)
        │           └── scripts/ai/ (AISynthesizer)
        └── scripts/trash/
```

---

## Testing

All backend modules are covered by the 100-test suite in `tests/`. Each sub-package has dedicated test modules:

```
tests/
├── test_ai_config.py           AI provider, rate limiting, cache
├── test_github_sync.py         Git remote push, credentials
├── test_phase5.py              End-to-end HTTP server integration
├── test_storage.py             SQLite WAL, rotation, metadata
├── test_v10_physical_rollback_and_selective_trash.py
│                               Rollback + trash integration
└── test_*.py                   Additional unit tests
```

```bash
# Run all tests
python -m unittest discover tests

# Run a specific module
python -m unittest tests.test_ai_config -v
```
