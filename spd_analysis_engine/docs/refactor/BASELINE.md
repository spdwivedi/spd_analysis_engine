# SPD Analysis Engine — Implementation Baseline

**Document Version:** 1.0.0  
**Generated Date:** 2026-09-30  
**Environment:** Windows (AMD64), Python 3.14.3, PowerShell 7 / Windows Terminal  
**Engine Root:** `d:\Project_Files\SPD_Analysis_Engine\spd_analysis_engine`  
**Status:** Frozen Implementation Baseline (Pre-Refactor)

---

## 1. Executive Summary & Authoritative Grounding

This document establishes the comprehensive technical baseline for the **SPD Analysis Engine** codebase prior to any architectural refactoring. All findings in this document are grounded strictly in the **actual repository source code**, which supersedes any documentation claims in existing READMEs or notes where discrepancies exist.

### Core Invariants Preserved
1. **Local-First Architecture:** The engine operates locally without cloud orchestration, Redis, Celery, microservices, or external web frameworks.
2. **Framework Stance:** Built on Python standard library HTTP servers (`http.server.ThreadingHTTPServer`), native SQLite (`sqlite3` in WAL mode), `psutil`, `watchdog`, and local child processes. No migration to FastAPI, Flask, Django, or WebSockets is performed.
3. **Public Contract Stability:** CLI commands (`python start.py <cmd>`), REST API routes, filesystem directory tiers (`current/`, `last_run/`, `history/`, `trash/`), and frontend DOM/SSE bindings remain preserved.
4. **Data Safety Guarantee:** Zero data loss. No user data, active sessions, history runs, patches, shadow repositories, or databases are modified or deleted.

---

## 2. Repository Inventory & Exclusion Audit

### 2.1 Complete Repository Inventory

| Area / Directory | Files Count | Key Modules & Components | Primary Responsibilities |
| :--- | :---: | :--- | :--- |
| **Root** | 10 | `start.py`, `requirements.txt`, `audit_engine.ps1`, `bundle_codebase.py`, `bundle_codebase.ps1`, `dashboard.html` | CLI dispatch, environment setup, bundling tools |
| **`scripts/` (Root Facades)** | 14 | `project_worker.py`, `diff_calc.py`, `event_bundler.py`, `watcher_fs.py`, `watcher_proc.py`, `storage_rotator.py`, `orchestrator.py`, `trash_manager.py`, `git_shadow.py`, `shell_interceptor.py`, `ai_analyzer.py`, `web_server.py`, `ide_profiles.py` | Backward-compatibility facades & worker entrypoints |
| **`scripts/core/`** | 12 | `supervisor.py`, `spawner.py`, `reaper.py`, `worker_lifecycle.py`, `worker_bundle.py`, `worker_entry.py`, `worker_helpers.py`, `worker_interlock.py`, `process_sweeper.py`, `preflight.py`, `scaffolder.py` | Multi-worker supervisor, process spawning, lifecycle management, dead worker reaping, process sweeping |
| **`scripts/storage/`** | 9 | `db.py` (`SessionDB`), `rotator.py` (`StorageRotator`), `meta.py`, `history.py`, `rollback.py`, `gitignore_manager.py`, `utils.py` | SQLite WAL database management, 3-tier storage rotation, history consolidation, physical rollback |
| **`scripts/git/`** | 9 | `shadow_repo.py` (`ShadowGit`), `shadow_push.py`, `shadow_rollback.py`, `credentials.py` (`GitCredentialManager`), `milestone_tracker.py`, `auto_pusher.py`, `remote_sync.py`, `utils.py` | Isolated Git micro-versioning, atomic commits, credentials, upstream syncing |
| **`scripts/server/`** | 14 | `server_daemon.py` (`EngineWebServer`), `request_handler.py` (`_EngineRequestHandler`), `routes_projects.py`, `routes_projects_resume.py`, `routes_events.py`, `routes_events_export.py`, `routes_ai.py`, `routes_ai_analysis.py`, `routes_ai_batch.py`, `routes_trash.py`, `routes_health.py`, `routes_workspace.py`, `batch_runner.py`, `db_queries.py` | Native HTTP daemon, SSE client queues, REST routing mixins |
| **`scripts/shell/`** | 4 | `tracker.py` (`ExecutionTracker`), `classifier.py`, `cmd_parser.py` | Terminal command tracking, prompt classification, command-derived READ event parsing |
| **`scripts/ai/`** | 14 | `synthesizer.py` (`AISynthesizer`), `synthesizer_tiered.py`, `synthesizer_burst.py`, `synthesizer_file.py`, `provider_gemini.py`, `provider_ollama.py`, `config.py`, `rate_limiter.py`, `cache_manager.py`, `prompt_builder.py`, `changelog.py`, `command_explainer.py` | LLM synthesis, multi-tier AI failover, rate limiting, disk caching, prompt construction |
| **`scripts/trash/`** | 9 | `manager.py` (`TrashManager`), `project_trash.py`, `purge.py`, `restore.py`, `fs_ops.py`, `burst_trash.py`, `remote_gh.py`, `constants.py` | Soft-deletion, project restoration, time-based purging, GitHub remote cleanup |
| **`web/`** | 24 | `index.html`, `app.js`, `style.css`, `css/*.css` (6 files), `js/*.js` (7 files), `js/views/*.js` (3 files), `js/views/modals/*.js` (6 files) | Single-page UI portal, SSE live streaming, Diff Viewer, Timeline, Modals |
| **`tests/`** | 27 | 24 functional & lifecycle test suites (`test_phase4.py`, `test_phase5.py`, `test_v6`–`test_v21`), `mock_activity.py`, + new `test_refactor_baseline_regressions.py` | Unit, integration, and lifecycle test coverage |
| **`notes/`** | 5 | Guides 01 through 05 | Operational documentation and developer notes |

### 2.2 Bundling & False Exclusion Audit

#### Defect Identified
In both `bundle_codebase.py` and `bundle_codebase.ps1`, directory exclusion rules improperly contained the token `"trash"` in a generic segment filter (`EXCLUDE_DIR_PARTS = { ... "trash" ... }` and `$excludePatterns = @( ... "trash" ... )`). 
Because Python's `os.walk` and PowerShell's regex matching checked each path part indiscriminately:
- Every file inside `scripts/trash/` (`manager.py`, `purge.py`, `restore.py`, `project_trash.py`, `fs_ops.py`, `burst_trash.py`, `remote_gh.py`, `constants.py`, `__init__.py`) was **falsely excluded** from codebase bundles.
- Any valid test fixtures or source paths named `patches`, `baseline`, or `trash` were vulnerable to silent omissions.

#### Remediation Applied
`bundle_codebase.py` and `bundle_codebase.ps1` were updated to distinguish between:
1. **Anywhere Exclusions (VCS, Environments, Caches):** `__pycache__`, `.git`, `shadow_git`, `.venvconda`, `.venv`, `venv`, `mock_workspace`, `.pytest_cache`, `.mypy_cache`, `.idea`, `.vscode`, `node_modules`.
2. **Root-Level Exclusions (Data Tiers Only):** Excluded ONLY when appearing as a root-level segment relative to `ENGINE_ROOT`: `current`, `last_run`, `history`, `trash`, `_backup_monoliths`, `ai_data/cache`.
3. **Secret Files & Large Databases:** `models_config.json`, `git_config.json`, `.env*`, `*.db`, `*.db-wal`, `*.db-shm`, `*.log`, `*.pid*`, `*.lock`, `*.wal`, `*.shm`, `*.stale`, `*.tmp`, `*.key`, `*.pem`, `*.pfx`.

**Result:** Verified that `bundle_codebase.py` now bundles all **145** active source, test, and web files (including all 9 `scripts/trash/` modules), while completely ignoring secret files and runtime databases.

### 2.3 Secret & Credential Handling Audit

| File Pattern | Git Tracking Status | Bundler Status | Sensitive Data Contained |
| :--- | :--- | :--- | :--- |
| `ai_data/models_config.json` | Ignored via `.gitignore` (`git check-ignore` PASS) | Excluded by name | Gemini API keys, model preferences |
| `ai_data/models_config.example.json` | Tracked in Git | Included in bundle | Sanitized placeholder config |
| `ai_data/git_config.json` | Ignored via `.gitignore` (`git check-ignore` PASS) | Excluded by name | GitHub Personal Access Tokens (PAT) |
| `ai_data/git_config.example.json` | Tracked in Git | Included in bundle | Sanitized placeholder config |
| `.env`, `*.env` | Ignored via `.gitignore` (`git check-ignore` PASS) | Excluded by regex | Environment variables & secrets |

---

## 3. Current Architecture Map & Actor Matrix

```
                      +---------------------------------------+
                      |          CLI Entry (start.py)         |
                      +---------------------------------------+
                        /                 |                 \
           start/stop/status/list         |                  ui
                      /                   |                   \
+-------------------------+               |         +-------------------------+
| Supervisor Instance #1  |               |         |    EngineWebServer      |
| - Registry & Spawner    |               |         | - Server Daemon & Port  |
| - ReaperDaemon #1       |               |         | - Supervisor Instance #2|
+-------------------------+               |         | - ReaperDaemon #2       |
            |                             |         +-------------------------+
            | (spawns detached process)   |                      |
            v                             |                      v
+-------------------------+               |         +-------------------------+
|     project_worker      |               |         |  _EngineRequestHandler  |
| - worker_lifecycle      |               |         | - REST Route Mixins     |
| - EventBundler          |<--------------+         | - SSE Client Queues     |
| - DiffCalculator        |                         +-------------------------+
| - ProjectWatcher (FS)   |                                      |
| - ProcessInspector      |                                      |
| - SessionDB (WAL Writer)|                                      v
+-------------------------+                         +-------------------------+
            |                                       |       Web Portal        |
    (persists state)                                | - Dashboard / Timeline  |
            v                                       | - Diff Viewer & Modals  |
+------------------------------------+              +-------------------------+
| Storage Tiers:                     |
| current/ -> last_run/ -> history/  |
| session.db, patches/, shadow_git/  |
+------------------------------------+
```

### 3.1 Subsystem Summary

- **CLI (`start.py`):** Thin command-line layer using `argparse`. Commands: `start`, `stop`, `status`, `list`, `ui`. Instantiates a global `Supervisor` on invocation.
- **Supervisor (`scripts/core/supervisor.py`):** Discovers running workers via `current/<slug>/worker.pid` files. Spawns workers via `spawner.py`, stops workers via signals/PID checks, and spawns a background `ReaperDaemon` thread running every 5.0 seconds.
- **Worker (`scripts/project_worker.py` + `scripts/core/`):** Detached subprocess. Wires filesystem watcher (`watchdog`), process inspector (`psutil`), event bundler, diff calculator, and `SessionDB`. Emits heartbeats into `current/<slug>/worker.status.json`.
- **EventBundler (`scripts/event_bundler.py`):** Aggregates raw file system notifications with a sliding debounce timer (default 3.5s quiet period). Dispatches flushed bundles to `_handle_event_bundle`.
- **SessionDB (`scripts/storage/db.py`):** SQLite connection in WAL mode (`journal_mode = WAL`, `synchronous = NORMAL`). Manages `sessions`, `events`, `patches`, and `git_milestones` tables.
- **ShadowGit (`scripts/git/shadow_repo.py`):** Maintains an isolated git repository in `current/<slug>/shadow_git/` pointing to the monitored workspace root via `--git-dir` and `--work-tree`. Creates micro-commits for each burst event.
- **Storage Rotation (`scripts/storage/`):** 3-tier lifecycle:
  - `current/<slug>/` — active monitoring session
  - `last_run/<slug>/` — most recently concluded session
  - `history/<slug>/run_<timestamp>/` — archived past sessions
- **AI Synthesis (`scripts/ai/`):** Tiered failover architecture: Tier 1 (Heuristic / AST diffs), Tier 2 (Ollama local LLM), Tier 3 (Google Gemini API). Managed with rate-limiting and persistent JSON disk caching.
- **Web Server (`scripts/server/`):** Standard library `ThreadingHTTPServer` serving static HTML/JS/CSS from `web/`, dispatching REST requests through multiple-inheritance mixins, and pushing events over Server-Sent Events (`/api/stream`).
- **Trash & Purge (`scripts/trash/`):** Soft deletion moving projects from any tier into `trash/<slug>_<timestamp>/`. Includes restoration and 30-day age purge logic.

---

### 3.2 Authoritative Actor Matrix

The following table identifies **every location in source code** capable of initiating state transitions, mutating storage, or calling external subsystems:

| Action | Source File(s) | Functions / Methods | Trigger Condition |
| :--- | :--- | :--- | :--- |
| **Start a worker** | `scripts/core/supervisor.py`<br>`scripts/core/spawner.py`<br>`scripts/server/routes_projects.py` | `Supervisor.start_project()`<br>`spawn_detached_worker()`<br>`_handle_api_start_project()` | CLI `start.py start` or POST `/api/projects/start` |
| **Stop a worker** | `scripts/core/supervisor.py`<br>`scripts/core/spawner.py`<br>`scripts/server/routes_projects.py` | `Supervisor.stop_project()`<br>`Supervisor.stop_all()`<br>`send_stop()` / `kill_pid()`<br>`_handle_api_stop_project()` | CLI `start.py stop`, server shutdown, or POST `/api/projects/stop` |
| **Declare a worker dead** | `scripts/core/reaper.py`<br>`scripts/core/supervisor.py`<br>`scripts/core/worker_helpers.py` | `ReaperDaemon._run()`<br>`Supervisor.get_status()`<br>`verify_worker_pid()` | PID is missing, not alive in `psutil`, or heartbeat age exceeds grace threshold |
| **Archive a project** | `scripts/storage/rotator.py`<br>`scripts/core/supervisor.py`<br>`scripts/core/reaper.py`<br>`scripts/server/routes_projects.py` | `StorageRotator.archive_project()`<br>`Supervisor.stop_project()`<br>`ReaperDaemon._run()`<br>`routes_projects._handle_api_projects()` | Worker stopped, worker died, or lingering inactive folder detected on GET `/api/projects` |
| **Mutate session/history storage** | `scripts/storage/db.py`<br>`scripts/storage/rotator.py`<br>`scripts/storage/history.py`<br>`scripts/storage/meta.py`<br>`scripts/server/routes_events.py` | `SessionDB.record_event()`<br>`SessionDB.create_session()`<br>`StorageRotator.init_project()`<br>`consolidate_project_history()`<br>`update_project_meta()`<br>`synthesize_project_meta()` | Event bundling, session start/stop, history consolidation (invoked on GET `/api/events`), metadata updates |
| **Open a writable SQLite connection** | `scripts/storage/db.py`<br>`scripts/storage/history.py`<br>`scripts/server/routes_ai.py` | `SessionDB._open()` (`sqlite3.connect`)<br>`consolidate_project_history()`<br>`_handle_api_analyze_event()` | Database creation, writing events, history consolidation, writing AI summaries |
| **Create a Git commit** | `scripts/git/shadow_repo.py`<br>`scripts/git/auto_pusher.py`<br>`scripts/core/scaffolder.py` | `ShadowGit.commit_burst()`<br>`AutoPusher.check_and_push()`<br>`scaffold_workspace()` | Burst quiet period elapsed, milestone auto-push, workspace scaffold initialization |
| **Call an AI provider** | `scripts/ai/provider_gemini.py`<br>`scripts/ai/provider_ollama.py`<br>`scripts/server/batch_runner.py` | `call_gemini()`<br>`call_ollama()`<br>`run_async_batch()` | User-requested event/file/burst analysis or background batch analysis |
| **Emit SSE events** | `scripts/server/request_handler.py`<br>`scripts/server/routes_ai_analysis.py`<br>`scripts/server/routes_events.py` | `_broadcast_sse()`<br>`_handle_api_analyze_event()`<br>`_handle_api_git_push()` | Heartbeat ping, AI analysis ready, git push result, burst rollback |

---

## 4. Existing Test Suite Inventory

Prior to adding regression tests, the baseline repository contained **24 test modules** across `tests/`:

| Test Module | Lines | Primary Scope Covered |
| :--- | :---: | :--- |
| `tests/test_ai_config.py` | 445 | AI configuration reading, saving, validation, key masking |
| `tests/test_cumulative_history_and_specs.py` | 291 | Multi-session history queries, project specs endpoints |
| `tests/test_github_sync.py` | 535 | Remote GitHub connection testing, repo creation, PAT auth |
| `tests/test_phase4.py` | 332 | Shadow Git repository initialization, micro-commit creation |
| `tests/test_phase5.py` | 305 | End-to-end multi-worker concurrency, lifecycle transitions |
| `tests/test_trash_manager.py` | 346 | TrashManager listing, soft delete, restore, purge |
| `tests/test_v10_physical_rollback_and_selective_trash.py` | 260 | Disk rollback of bursts and selective project deletion |
| `tests/test_v11_ui_optimization_and_debounce.py` | 135 | Debounce sliding windows and fast-poll triggers |
| `tests/test_v12_health_and_diagnostics.py` | 197 | System and pipeline health endpoints (`/api/engine/health`) |
| `tests/test_v13_self_healing_resume_and_locks.py` | 275 | Auto-recovery from ungraceful termination and lock clearing |
| `tests/test_v14_micromodules_scaffold_gitignore_autopush.py` | 346 | Scaffolding, baseline gitignore generation, auto-push logic |
| `tests/test_v15_scaffolding_and_autopush.py` | 352 | Scaffolder templates and auto-push configuration |
| `tests/test_v16_milestone_scoping_and_history.py` | 200 | Milestone aggregation and burst range selection |
| `tests/test_v17_sqlite_resilience_and_tier_archiving.py` | 205 | SQLite busy timeout handling and 3-tier archive migration |
| `tests/test_v18_safe_rollback_and_ai_circuit_breaker.py` | 195 | AI circuit breaker engaging Tier 1 heuristic failovers |
| `tests/test_v19_worker_interlock_and_lock_elimination.py` | 189 | Worker PID interlock and duplicate prevention |
| `tests/test_v20_full_lifecycle_matrix.py` | 296 | Full state matrix across start, edit, stop, crash, rotate |
| `tests/test_v20_process_sweeper_and_hot_reload.py` | 178 | Sweeping lingering worker processes on restart |
| `tests/test_v21_path_identity_and_session_reuse.py` | 187 | Canonical slug resolution and path-identity matching |
| `tests/test_v6_upgrades.py` | 200 | Upgrades for file metadata and debounce parameters |
| `tests/test_v7_ide_profiles.py` | 341 | Process whitelisting for Antigravity, Cursor, Windsurf |
| `tests/test_v8_adaptive_polling_and_command_ai.py` | 296 | Command tracking and command-derived explanation prompts |
| `tests/test_v9_crash_rollback_and_lap.py` | 367 | Crash detection, ungraceful exit detection, recovery |
| `tests/test_web_server.py` | 192 | Web server binding, port incrementing, basic routes |

### Baseline Test Execution Results
- **Runner:** `python -m unittest discover tests`
- **Total Tests Discovered:** 180
- **Passed:** 180
- **Failed:** 0
- **Errors:** 0
- **Execution Time:** 54.530s

---

## 5. Confirmed Architectural Deficiencies & RED Regression Test Inventory

To ensure that refactoring safely resolves architectural defects without regressions, **17 deterministic regression tests** have been implemented in `tests/test_refactor_baseline_regressions.py`. Each test reproduces an observed bug and **currently FAILS (RED)** against the unrefactored baseline.

### 5.1 Defect Details & RED Regression Test Matrix

| ID | Confirmed Defect | Root Cause in Code | Regression Test Method | Test Status |
| :---: | :--- | :--- | :--- | :---: |
| **01** | Only one Supervisor in UI mode | `start.py:main` instantiates `_get_supervisor()`. `start.py:cmd_ui` then instantiates `EngineWebServer`, which in `__init__` instantiates a *second* `Supervisor(self.engine_root)`. Two independent `ReaperDaemon` threads run simultaneously. | `test_regression_01_only_one_supervisor_in_ui_mode` | **RED (FAIL)** |
| **02** | `GET /api/projects` mutates state | `routes_projects.py:_handle_api_projects` (lines 145–165) iterates through `current/` and calls `rotator.archive_project(slug)` on inactive folders. A read-only GET mutates disk. | `test_regression_02_get_api_projects_does_not_mutate_state` | **RED (FAIL)** |
| **03** | `GET /api/events` invokes consolidation / mutation | `routes_events.py:_handle_api_events` (line 68) calls `consolidate_project_history()`, which opens writable connections, executes `INSERT INTO events`, and copies patch files on disk. | `test_regression_03_events_get_does_not_invoke_consolidation_mutation` | **RED (FAIL)** |
| **04** | Heartbeat written in IST is incorrectly interpreted as Z | `worker_lifecycle.py` formats status timestamps with `_now_ist_iso()` (`+05:30`). `supervisor.py` lines 393–394 attempts to parse with `strptime(..., "%Y-%m-%dT%H:%M:%SZ")`, raising `ValueError` and setting `last_hb_s = None` (or drifting by 19,800s if parsed as UTC). | `test_regression_04_heartbeat_written_in_ist_interpreted_correctly` | **RED (FAIL)** |
| **05** | Basename substring process matching is unsafe | `process_sweeper.py` uses `if clean_slug in val:` and `norm_target in full_cmd_str`. Sweeping project `"demo"` inadvertently matches and kills unrelated processes like `"demo_extended"`. | `test_regression_05_basename_substring_process_matching_unsafe` | **RED (FAIL)** |
| **06** | Difflib patch output is rejected by `git apply --check` | `diff_calc.py:compute_diff` keeps line endings with `splitlines(keepends=True)` and then joins with `"\n".join(diff_lines) + "\n"`. This creates corrupt double newlines (`\n\n`), failing with `error: corrupt patch at <stdin>:6`. | `test_regression_06_difflib_patch_accepted_by_git_apply_check` | **RED (FAIL)** |
| **07** | `EventBundler` timer race | `threading.Timer.cancel()` in `add_event` cannot stop a timer that has already entered `_on_timer_fired()`. The expiring callback steals newly-arrived events inside `_extract_bundle()` without giving them their quiet period. | `test_regression_07_event_bundler_timer_race_does_not_steal_pending_event` | **RED (FAIL)** |
| **08** | Shared SQLite concurrent writer race / corruption | `SessionDB` opens connection with `check_same_thread=False` and manages `BEGIN IMMEDIATE` / `COMMIT` without a threading mutex. Concurrent thread writes crash with `OperationalError: cannot start a transaction within a transaction`. | `test_regression_08_shared_sqlite_concurrent_writers` | **RED (FAIL)** |
| **09** | Duplicate READ events | `ProcessInspector` scans every 1.0s and calls `record_event(..., event_type="READ")`. `events` table lacks deduplication constraints, inserting multiple identical rows for the same file in the same session. | `test_regression_09_duplicate_read_events` | **RED (FAIL)** |
| **10** | 2,000-file Git operation command line overflow | `ShadowGit.commit_burst` calls `self._run_git("add", "--", *existing)`. On Windows, 2,000 path arguments exceed the 32,767-character limit of `CreateProcess`, crashing with `[WinError 206] The filename or extension is too long`. | `test_regression_10_git_operation_2000_files` | **RED (FAIL)** |
| **11** | GET on history-only project creates `current/<slug>/` | Querying a history-only project calls `resolve_project_context`, which calls `synthesize_project_meta`, which unconditionally writes `current/<slug>/project.meta` and creates `current/<slug>/`. | `test_regression_11_get_history_only_project_creates_current_dir` | **RED (FAIL)** |
| **12** | Stale Supervisor archiving a replacement worker | `ReaperDaemon` detects that an entry in its registry died. It blindly executes `self._rotator.archive_project(name)` without checking if `current/<slug>/worker.pid` has been replaced by a live, active worker PID. | `test_regression_12_stale_supervisor_archiving_replacement_worker` | **RED (FAIL)** |
| **13** | Duplicate event collapse from second-resolution timestamps | `_utcnow_iso()` formats timestamps as `%Y-%m-%dT%H:%M:%SZ` without microseconds. `consolidate_project_history` deduplicates via `(timestamp, event_type, first_file, summary)`, collapsing distinct rapid edits occurring in the same second into 1. | `test_regression_13_duplicate_event_collapse_second_resolution` | **RED (FAIL)** |
| **14** | Worker startup blocking before status publication | `wire_worker()` synchronously captures the workspace baseline (`DiffCalculator.capture_baseline()`) before `worker.status.json` is published. On large workspaces, supervisor times out waiting for status. | `test_regression_14_worker_startup_publishes_status_before_blocking` | **RED (FAIL)** |
| **15** | Unbounded `worker.log` | `project_worker._setup_logging()` opens `worker.log` with `open(log_path, "a")` and attaches an unconstrained `StreamHandler`. There is no `RotatingFileHandler` or size cap; logs grow indefinitely. | `test_regression_15_unbounded_worker_log` | **RED (FAIL)** |
| **16** | Worker importing server/API/AI credential machinery | `scripts/__init__.py` imports `EngineWebServer`, `ShadowGit` (which imports `GitCredentialManager`), and `AISynthesizer` (importing LLM SDKs and key loaders). Worker modules importing `scripts.*` pollute child workers with server/AI code. | `test_regression_16_worker_import_isolation` | **RED (FAIL)** |
| **17** | README / API route drift | `scripts/server/README.md` documents `POST /api/project/start`, `POST /api/project/<n>/stop`, and `GET /api/events/stream`. In code, `request_handler.py` only implements `/api/projects/start`, `/api/projects/stop`, and `/api/stream`. | `test_regression_17_readme_api_route_drift` | **RED (FAIL)** |

### 5.2 Verification of Regression Test Suite Execution
- **Command:** `python -B -m unittest tests.test_refactor_baseline_regressions`
- **Results:** `Ran 17 tests in 1.831s`
- **Failures:** `17`
- **Errors:** `0`
- **Passes:** `0`
- **Status:** **100% RED** as required for implementation baseline verification.

---

## 6. Public Contracts Inventory

To ensure subsequent refactorings do not break backward compatibility, the following contracts are documented as binding:

### 6.1 Public CLI Contract (`start.py`)

| Command | Arguments | Flags | Output Modes | Exit Codes |
| :--- | :--- | :--- | :--- | :--- |
| `start` | `--project, -p <name>` (req)<br>`--path <dir>` (req) | `--scaffold`<br>`--debounce <float>` (def 3.5)<br>`--[no-]track-reads` (def True)<br>`--[no-]track-exec` (def True)<br>`--[no-]shadow-git` (def True)<br>`--git-init-primary`<br>`--ide-profile <name>`<br>`--ide-custom-marker <str>`<br>`--json`<br>`-v, --verbose` | Formatted ANSI table or newline JSON | 0: Success<br>1: User Error<br>2: Engine Error |
| `stop` | `--project, -p <name>` (req) | `--json`<br>`-v, --verbose` | Formatted ANSI tag or JSON | 0: Success<br>1: Project Not Found<br>2: Engine Error |
| `status` | `--project, -p <name>` (opt) | `--json`<br>`-v, --verbose` | Formatted ANSI worker table or JSON | 0: Success |
| `list` | None | `--json`<br>`-v, --verbose` | Formatted registry table or JSON | 0: Success |
| `ui` | None | `--port <int>` (def 8765)<br>`--no-browser`<br>`-v, --verbose` | Console banner + HTTP daemon | 0: Clean Shutdown |

### 6.2 REST API Route Contract (`request_handler.py`)

| Method | Endpoint Pattern | Handled By | Input Body / Query | Expected Output |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/api/status` | `_handle_api_status` | None | `{status: "ok", active_count: N, workers: [...]}` |
| `GET` | `/api/projects` | `_handle_api_projects` | None | `{current: [...], last_run: [...], history: [...]}` |
| `POST` | `/api/projects/start` | `_handle_api_start_project` | JSON `{project, path, debounce, ...}` | `{status: "ok", pid: N, log_path: "..."}` |
| `POST` | `/api/projects/stop` | `_handle_api_stop_project` | JSON `{project: "name"}` | `{status: "stopped", pid: N}` |
| `POST` | `/api/projects/resume` | `_handle_api_resume_project` | JSON `{project: "name"}` | `{status: "ok", pid: N}` |
| `GET` | `/api/project/<slug>/events` | `_handle_api_events` | Query: None | `{project: slug, tier: "...", events: [...]}` |
| `GET` | `/api/project/<slug>/sessions` | `_handle_api_sessions` | Query: None | `{project: slug, sessions: [...]}` |
| `GET` | `/api/project/<slug>/logs` | `_handle_api_logs` | Query: `?lines=N` | `{project: slug, lines: [...]}` |
| `GET` | `/api/project/<slug>/specs` | `_handle_api_project_specs` | None | `{project: slug, specs: {...}}` |
| `GET` | `/api/project/<slug>/shadow-git` | `_handle_api_shadow_git` | Query: `?limit=N` | `{commits: [...]}` |
| `GET` | `/api/project/<slug>/milestones` | `_handle_api_project_milestones` | None | `{milestones: [...]}` |
| `POST` | `/api/project/<slug>/seal-burst` | `_handle_api_seal_burst` | JSON `{burst_id: N}` | `{status: "sealed", commit: "..."}` |
| `POST` | `/api/project/<slug>/rollback-burst`| `_handle_api_rollback_burst` | JSON `{burst_id: N}` | `{status: "rolled_back", files: [...]}` |
| `GET` | `/api/trash` | `_handle_api_get_trash` | None | `{trash: [...]}` |
| `POST` | `/api/trash/restore` | `_handle_api_restore_trash` | JSON `{trash_id: "..."}` | `{status: "restored"}` |
| `POST` | `/api/trash/purge` | `_handle_api_purge_trash` | JSON `{trash_id: "..."}` | `{status: "purged"}` |
| `GET` | `/api/ai/config` | `_handle_api_get_ai_config` | None | `{configured: bool, provider: "...", masked_keys}` |
| `POST` | `/api/ai/config` | `_handle_api_save_ai_config` | JSON `{api_key, model, ...}` | `{status: "saved"}` |
| `GET` | `/api/git/config` | `_handle_api_get_git_config` | None | `{configured: bool, username: "...", masked_pat}` |
| `POST` | `/api/git/config` | `_handle_api_save_git_config` | JSON `{pat, username, ...}` | `{status: "saved"}` |
| `GET` | `/api/engine/health` | `_handle_api_engine_health` | None | `{status: "healthy", pipelines: {...}}` |
| `GET` | `/api/stream` | `_handle_api_stream` | Header `Accept: text/event-stream` | SSE keepalive and event stream |

### 6.3 Storage & Filesystem Layout Contract

```
<engine_root>/
  ├── current/<slug>/
  │     ├── session.db            (SQLite in WAL mode)
  │     ├── session.db-wal
  │     ├── session.db-shm
  │     ├── worker.pid            (Plaintext: PID\nUTC_ISO)
  │     ├── worker.status.json    (Atomic heartbeat payload)
  │     ├── worker.log            (Redirected stdout/stderr)
  │     ├── project.meta          (Key=value config pairs)
  │     ├── patches/              (Combined burst patches)
  │     ├── baseline/             (Pre-burst file snapshots)
  │     └── shadow_git/           (Isolated git repository)
  ├── last_run/<slug>/            (Same layout as current/ for previous session)
  ├── history/<slug>/
  │     └── run_<YYYYMMDD_HHMMSS_micros>/  (Archived historical sessions)
  ├── trash/<slug>_<timestamp>/   (Soft-deleted projects with manifest)
  └── ai_data/
        ├── models_config.json    (API keys & model config - NOT TRACKED)
        ├── git_config.json       (Git PAT & user config - NOT TRACKED)
        └── cache/                (Disk-cached LLM responses)
```

---

## 7. Platform-Specific Requirements & Windows Matrix

The target runtime environment is **Windows (AMD64)** with Python 3.14. The following behavior matrix details which subsystems rely on Windows-specific APIs versus portable abstractions:

| Subsystem | Windows API / Mechanism | Behavior on Windows | Fallback / Cross-Platform Path | Test Requirement |
| :--- | :--- | :--- | :--- | :--- |
| **Worker Process Spawning** | WMI via PowerShell (`Invoke-CimMethod -ClassName Win32_Process`) or `CREATE_NO_WINDOW` (0x08000000) | Spawns completely detached background process without opening console window. | `subprocess.Popen(start_new_session=True)` on POSIX. | **Requires Windows** for WMI / creation flags validation. |
| **Process Termination** | `taskkill /F /PID <pid>` or `psutil.Process.terminate()` | Overcomes Windows handle locks by issuing force termination when child processes hang. | `os.kill(pid, signal.SIGTERM)` on POSIX. | **Requires Windows** for `taskkill` verifying clean exit. |
| **Process Handle Scanning** | `psutil.Process.open_files()` | Scans Win32 open file handles of IDEs (e.g. Cursor, Antigravity) to detect READ operations. | Reads `/proc/<pid>/fd` on Linux. | **Requires Windows** for native IDE process matching (`.exe`). |
| **Atomic File Replacement** | Win32 file locking (`WinError 32: The process cannot access the file because it is being used by another process`) | `safe_write_status_json` implements exponential backoff to handle Windows antivirus / search indexer lock conflicts. | Standard `Path.replace()` on POSIX (atomic rename). | **Requires Windows** to test lock retry under concurrent reads. |
| **Git Command Arguments** | Win32 `CreateProcess` command-line limit (32,767 characters) | Passing thousands of files to `git add --` fails with `[WinError 206]`. | `ARG_MAX` (typically 2MB+) on Linux. | **Requires Windows** for testing 2,000-file command-line chunking. |
| **Directory Cleanup** | `shutil.rmtree` read-only attribute trap | Git object files on Windows frequently have `stat.S_IREAD` set, failing standard `rmtree`. `_remove_tree_force` clears `S_IWRITE` via `onexc`. | Standard `rmtree` on POSIX. | **Requires Windows** for verifying Git object deletion. |

---

## 8. Data Safety & Non-Destructive Backup Procedure

To protect existing project data (`current/`, `last_run/`, `history/`, `ai_data/`) from accidental loss or corruption during upcoming refactoring phases, the following **non-destructive backup procedure** is established.

### Non-Destructive Backup Execution

```powershell
# 1. Define backup timestamp and destination outside the active workspace
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = "d:\Project_Files\SPD_Analysis_Engine\_backups\snapshot_$timestamp"

# 2. Create target directory
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null

# 3. Non-destructive mirror using robocopy (preserves timestamps, handles hidden files)
robocopy current "$backupRoot\current" /E /ZB /R:3 /W:1 /XF *.tmp
robocopy last_run "$backupRoot\last_run" /E /ZB /R:3 /W:1 /XF *.tmp
robocopy history "$backupRoot\history" /E /ZB /R:3 /W:1 /XF *.tmp
robocopy ai_data "$backupRoot\ai_data" /E /ZB /R:3 /W:1 /XF *.tmp

Write-Host "Snapshot successfully preserved at: $backupRoot" -ForegroundColor Green
```

### Safety Rules
- **NEVER** run `git clean -fd`, `git reset --hard`, or destructive Git commands in the repository.
- **NEVER** delete `current/`, `last_run/`, `history/`, or `trash/` directories.
- **NEVER** expose API keys, credentials, or `.env` files in source bundles or logs.

---

## 9. Baseline Telemetry & Measurements

Measurements taken on the unrefactored system baseline (2026-09-30):

| Metric | Measured Baseline | Target in Refactor | Analysis & Notes |
| :--- | :---: | :---: | :--- |
| **Existing Test Suite (Pass / Total)** | 180 / 180 (100%) | 180 / 180 (100%) | Complete existing test suite runs in 54.53s. |
| **Regression Suite (Red / Total)** | 17 / 17 (100% RED) | 0 / 17 (All Green) | All 17 architectural failure modes reproduced deterministically. |
| **Worker Spawn Latency** | 525.76 ms | < 150 ms | Spawning detached worker via PowerShell WMI takes ~0.5s. |
| **Worker Stop Latency** | 1,269.69 ms | < 150 ms | Hardcoded `time.sleep(1.2)` artificially inflates stop duration. |
| **Status Request Latency (Internal)** | 0.08 ms | < 1.0 ms | Fast in-memory supervisor dictionary lookup. |
| **Events API Payload Size (`spcl`)** | 4,572,766 bytes (4.47 MB) | < 100 KB (metadata)<br>On-demand diffs | 6 events currently transfer 4.47 MB because all diffs/patches are embedded in one JSON array. |
| **Process Sweeper Scan Duration** | 32.01 ms | < 10 ms | Iterating all OS processes with psutil takes ~32ms on Windows. |
| **Idle Process CPU Usage** | 0.00% | 0.00% | Zero CPU overhead when idle. |
| **Active SQLite DB Size (`spcl`)** | 4,472,832 bytes (4.37 MB) | Compact | `session.db` holding prompt bursts. |
| **Active SQLite WAL Size (`spcl`)** | 57,712 bytes (56.4 KB) | Clean Checkpoint | Uncommitted transactions in WAL. Historical runs have up to 4.15 MB WAL. |
| **Worker Log Size (`spcl`)** | 20,652 bytes (20.2 KB) | Bounded | Currently unbounded append-only file. |
| **Reaper Threads in UI Mode** | 2 threads (`spd-reaper`) | 1 thread | Supervisor duplication causes 2 concurrent reaper threads in UI mode. |

---

## 10. Conclusion & Next Steps

This baseline establishes an authoritative record of the system's operational realities, contracts, and verified failures. With 180 baseline tests passing, 17 regression tests failing (RED), and data safety secured, the codebase is fully prepared for structured, non-breaking refactoring in subsequent phases.
