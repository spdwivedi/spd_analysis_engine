Step_01: Audit Repository Structure
ID: S00-M01-P01-Step01

Status: DONE

Depends on: none

1. Purpose
Perform an exhaustive inventory of the repository to identify all active source files, legacy scripts, frontend assets, tests, and configuration files.

2. Scope
spd_analysis_engine/start.py, spd_analysis_engine/scripts/, spd_analysis_engine/web/, spd_analysis_engine/tests/

3. Verification (Done when...)
Repository inventory document is completed, cataloging all modules and ensuring no unmapped source components exist.

## 4. Execution Notes / Result

### 4.1 Executive Summary
An exhaustive audit of the `spd_analysis_engine/` application plane was performed on 2026-10-04. The application represents a local-first Python engine featuring a multi-tier modular architecture, stdlib HTTP/SSE server, per-project SQLite session stores, Shadow Git versioning, and an extensive test suite.

### 4.2 Top-Level & Configuration Inventory
- **Entry Points & Automation:**
  - `start.py` (21,242 bytes): Master CLI orchestrator handling UI, worker, and server startup.
  - `bundle_codebase.py` (7,933 bytes) & `bundle_codebase.ps1` (7,910 bytes): Code bundling tools.
  - `audit_engine.ps1` (5,961 bytes): PowerShell engine audit utility.
  - `dashboard.html` (21,296 bytes): Standalone legacy dashboard interface.
  - `__init__.py` (927 bytes): Root package declaration.
- **Project Configuration & Metadata:**
  - `pyproject.toml` (793 bytes): Build system and package configuration.
  - `requirements.txt` (192 bytes): Python dependency specifications.
  - `.gitignore` (397 bytes): Repository ignore patterns.
  - `README.md` (16,737 bytes): Engine documentation.
  - `codebase_bundle.md` (1,523,096 bytes): Existing exported markdown bundle.

### 4.3 Script Modules Inventory (126 Python Files)
The Python scripts are organized into root facades and 8 specialized subpackages:
1. **Root Scripts (`scripts/`, 15 files):**
   - Core facades: `ai_analyzer.py`, `diff_calc.py`, `event_bundler.py`, `facade.py`, `git_shadow.py`, `ide_profiles.py`, `orchestrator.py`, `project_worker.py`, `shell_interceptor.py`, `storage_rotator.py`, `trash_manager.py`, `watcher_fs.py`, `watcher_proc.py`, `web_server.py`, `__init__.py`.
2. **Core / Execution (`scripts/core/`, 20 files):**
   - Worker management & event assembly: `burst_assembler.py`, `change_processor.py`, `changeset.py`, `faults.py`, `fs_event.py`, `preflight.py`, `process_sweeper.py`, `project_config.py`, `reaper.py`, `scaffolder.py`, `spawner.py`, `supervisor.py`, `time_utils.py`, `worker_bundle.py`, `worker_entry.py`, `worker_helpers.py`, `worker_interlock.py`, `worker_lifecycle.py`, `worker_status.py`, `__init__.py`.
3. **Storage Engine (`scripts/storage/`, 16 files):**
   - SQLite, schema migrations, and fingerprints: `db.py`, `fingerprint.py`, `gitignore_manager.py`, `history.py`, `ignore_policy.py`, `manifest.py`, `meta.py`, `migrator.py`, `project_storage.py`, `rollback.py`, `rotator.py`, `schema_migration.py`, `utils.py`, `writer.py`, `writer_ops.py`, `__init__.py`.
4. **Server & REST API (`scripts/server/`, 20 files):**
   - HTTP server, SSE streams, and routing: `api_contract.py`, `batch_runner.py`, `db_queries.py`, `request_handler.py`, `routes_ai.py`, `routes_ai_analysis.py`, `routes_ai_batch.py`, `routes_ai_jobs.py`, `routes_events.py`, `routes_events_api.py`, `routes_events_export.py`, `routes_health.py`, `routes_projects.py`, `routes_projects_resume.py`, `routes_trash.py`, `routes_workspace.py`, `security.py`, `server_daemon.py`, `sse_stream.py`, `__init__.py`.
5. **Controller Layer (`scripts/controller/`, 11 files):**
   - Central authority & reconciler: `control_channel.py`, `controller.py`, `jobs.py`, `lifecycle.py`, `locks.py`, `operations.py`, `process.py`, `reconciler.py`, `recovery.py`, `registry.py`, `__init__.py`.
6. **AI Subsystem (`scripts/ai/`, 21 files):**
   - Providers, prompt builders, caches: `cache_manager.py`, `cache_queries.py`, `changelog.py`, `command_explainer.py`, `config.py`, `controller_db.py`, `controller_executor.py`, `error_handling.py`, `identity.py`, `profiles.py`, `progressive_levels.py`, `prompt_builder.py`, `provider_gemini.py`, `provider_ollama.py`, `provider_pool.py`, `rate_limiter.py`, `sanitizer.py`, `synthesizer.py`, `synthesizer_burst.py`, `synthesizer_file.py`, `synthesizer_tiered.py`, `__init__.py`.
7. **Git Subsystem (`scripts/git/`, 10 files):**
   - Shadow Git & push jobs: `auto_pusher.py`, `credentials.py`, `milestone_tracker.py`, `remote_sync.py`, `shadow_commit.py`, `shadow_push.py`, `shadow_repo.py`, `shadow_rollback.py`, `utils.py`, `__init__.py`.
8. **Shell & Telemetry (`scripts/shell/`, 4 files):**
   - CLI interaction tracking: `classifier.py`, `cmd_parser.py`, `tracker.py`, `__init__.py`.
9. **Trash Management (`scripts/trash/`, 9 files):**
   - Safe deletion & recovery: `burst_trash.py`, `constants.py`, `fs_ops.py`, `manager.py`, `project_trash.py`, `purge.py`, `remote_gh.py`, `restore.py`, `__init__.py`.

### 4.4 Test Suite Inventory (52 Python Test Files)
The test suite covers lifecycle, storage, concurrency, and security:
- **Baseline & Lifecycle Tests:** `test_authoritative_lifecycle.py`, `test_lifecycle_authority.py`, `test_refactor_baseline_regressions.py`, `test_safe_worker_shutdown.py`, `test_v20_full_lifecycle_matrix.py`, `test_v13_self_healing_resume_and_locks.py`, `test_v19_worker_interlock_and_lock_elimination.py`.
- **Concurrency & Concurrency Race Tests:** `test_session_db_concurrency.py`, `test_single_writer_architecture.py`, `test_event_bundler_safety.py`, `test_burst_assembler_and_processor.py`.
- **Storage, Diff, & Git Tests:** `test_storage_migration.py`, `test_read_idempotency.py`, `test_diff_calc_git_apply.py`, `test_changeset_and_shadow_git.py`, `test_ignore_policy_manifest_watcher.py`, `test_rollback_and_autopush.py`, `test_v10_physical_rollback_and_selective_trash.py`.
- **Server, API, & SSE Tests:** `test_web_server.py`, `test_api_contract_events_sse.py`, `test_server_hardening.py`, `test_frontend_incremental.py`.
- **AI Subsystem Tests:** `test_ai_config.py`, `test_controller_ai_execution.py`, `test_provider_profiles_and_sanitizer.py`.
- **Telemetry & Process Scanner Tests:** `test_process_telemetry_safety.py`, `test_process_telemetry_redesign.py`, `test_v20_process_sweeper_and_hot_reload.py`.
- **Fault Injection & Invariants:** `test_fault_injection_and_invariants.py`.

### 4.5 Web Frontend Assets (35 Files)
Structured into HTML, modular CSS, and vanilla ES6 JavaScript:
- `web/index.html` (86,424 bytes): Single Page Application entry point.
- `web/style.css` & `web/css/` (6 CSS files: `cards.css`, `diff.css`, `eco_mode.css`, `layout.css`, `modals.css`, `variables.css`).
- `web/app.js` & `web/js/` (25 JS files):
  - Core: `dom.js`, `sidebar.js`, `stream.js`, `toasts.js`, `utils.js`, `eco_mode.js`.
  - API Client: `api/client.js`, `api/events.js`, `api/index.js`.
  - State Management: `state/store.js`, `state/index.js`.
  - Components: `components/windowed_list.js`, `components/index.js`.
  - Views: `views/dashboard.js`, `views/diff_viewer.js`, `views/timeline.js`, `views/modals.js`.
  - Modal Controllers: `modal_ai.js`, `modal_base.js`, `modal_git.js`, `modal_health.js`, `modal_trash.js`, `modal_workspace.js`.

### 4.6 Runtime Storage Directory Audit
The active data footprint was inspected:
- **`current/` (Active, 9 files, 4.59 MB):**
  - Contains active project workspace `spcl` with `session.db` (4.47 MB), active WAL files (`session.db-wal`, `session.db-shm`), `worker.log`, and `worker.status.json`.
- **`last_run/` (Active, 11 files, 5.52 MB):**
  - Contains previous run snapshots for 3 projects:
    - `nova_pulse`: 4 files, 999 KB.
    - `spcl`: 4 files, 4.53 MB.
    - `web_portal_test`: 3 files, 287 bytes.
- **`history/` (Active, 392 files, 30.96 MB):**
  - Contains historical run records:
    - `nova_pulse`: 364 files across multiple run timestamps (5.40 MB).
    - `spcl`: 27 files (25.56 MB).
- **`ai_data/` (Active, 166 files, 201 KB):**
  - Holds `models_config.json`, `git_config.json`, examples, and 161 cached JSON analysis files in `ai_data/cache/`.
- **`data/` (Active, 1 file, 61.44 KB):**
  - Holds `engine.db`, the central SQLite database for EngineController tracking.
- **`trash/` (0 files):** Directory exists but is currently empty.
- **`_backup_monoliths/` (6 files):** Historical monolithic script backups.
- **`.venvconda/` (1,550 files):** Local Python virtual environment; confirmed ignored.

### 4.7 Key Architectural Insights for Step 02
1. `bundle_codebase.py` must strictly exclude `.venvconda/`, `current/`, `last_run/`, `history/`, `ai_data/cache/`, `*.db`, `*.db-wal`, and sensitive JSON files (`models_config.json`, `git_config.json`).
2. `bundle_codebase.py` currently risks excluding files if directory names like `trash` or `patches` appear in relative paths. It must be updated to target only genuine source code and tests.
