# Core Execution Subsystem (`scripts/core/`)

## Module Purpose
The `core` module forms the execution engine of the SPD Analysis Engine. It is responsible for detached process orchestration, Windows process group spawning, cooperative shutdown management, condition-variable burst debouncing, normalized filesystem event processing, and configuration validation.

---

## File Roster & Technical Reference

- **`__init__.py`**
  - Package initialization and internal exports for core execution components.

- **`burst_assembler.py`**
  - **Classes:** `BurstAssembler`
  - **Core Functions:** `_utcnow_iso()`
  - **Purpose:** Condition-variable-based event aggregator replacing `threading.Timer` debouncing. Enforces dual-deadline waiting: `due = min(last_event + quiet_period, first_event + max_burst)`. Bounded at 20,000 events to prevent unbounded memory growth.

- **`change_processor.py`**
  - **Classes:** `ChangeProcessor`
  - **Purpose:** Single ordered consumer per project. Extracts sealed bursts from `BurstAssembler`, compares workspace manifests via file fingerprints, commits changes to Shadow Git, and enqueues event records to `ProjectWriter`.

- **`changeset.py`**
  - **Classes:** `ChangeSet`
  - **Core Functions:** `check_file_size_policy(file_path, max_bytes)`, `truncate_patch_content(patch_str, max_bytes)`
  - **Purpose:** Immutable domain object representing an atomic burst changeset. Enforces large-file policies (truncates text diffs > 1 MB, patches > 2 MB with SHA-256 metadata).

- **`faults.py`**
  - **Classes:** `InjectedFaultError`
  - **Core Functions:** `normalize_fault_name(name)`, `is_fault_active(name)`, `check_fault(name)`, `fault_injection(name)`
  - **Purpose:** Deterministic fault injection harness controlled via the `SPD_TEST_FAULTS` environment variable, enabling crash simulation across 11 key lifecycle boundaries.

- **`fs_event.py`**
  - **Classes:** `FsEvent`, `EventCoalescer`
  - **Core Functions:** `is_editor_temp_file(path)`
  - **Purpose:** Strongly-typed filesystem event definition and event coalescer that collapses rapid editor atomic save sequences into singular `MODIFY` events.

- **`preflight.py`**
  - **Core Functions:** `_colored(text, color)`, `clean_orphan_lockfiles(engine_root)`, `run_preflight_checks(engine_root)`
  - **Purpose:** Pre-flight diagnostic suite executed prior to UI or worker launch. Cleans stale `.lock` files and verifies network/port availability.

- **`process_sweeper.py`**
  - **Core Functions:** `sweep_project_processes(project_name, canonical_target, engine_root)`
  - **Purpose:** Scans running OS processes and safely terminates orphaned or zombie processes belonging to a project using exact multi-attribute validation.

- **`project_config.py`**
  - **Classes:** `ProjectConfig`
  - **Core Functions:** `_parse_bool(val)`, `parse_meta_text(text)`
  - **Purpose:** Validated dataclass defining project capture parameters (`debounce_s`, `max_burst_s`, `watch_policy`, `git_policy`, `ai_profile`). Enforces configuration precedence: CLI > project file > engine defaults.

- **`reaper.py`**
  - **Classes:** `ReaperDaemon`
  - **Purpose:** Non-authoritative background observer thread that tracks worker process heartbeats and logs unresponsive worker warnings.

- **`scaffolder.py`**
  - **Core Functions:** `_run_git(cmd, cwd)`, `scaffold_new_project(name, path, init_git)`
  - **Purpose:** Automates workspace directory creation, default `.gitignore` placement, and optional primary Git initialization.

- **`spawner.py`**
  - **Classes:** `SpawnResult`
  - **Core Functions:** `find_worker_pid_by_worker_id(worker_id)`, `build_worker_cmd(...)`, `spawn_detached_worker(...)`, `send_stop(proc)`, `send_stop_pid(pid)`, `kill_pid(pid)`, `wait_for_pid_exit(pid)`
  - **Purpose:** Spawns detached OS worker processes using `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows (with base64 encoded PowerShell fallback) and `setsid` on POSIX.

- **`supervisor.py`**
  - **Classes:** `Supervisor`
  - **Purpose:** Top-level orchestration supervisor providing high-level `start_project()`, `stop_project()`, `get_status()`, and `list_projects()` APIs consumed by `start.py`.

- **`time_utils.py`**
  - **Core Functions:** `utc_now_iso()`, `parse_iso_timestamp(ts_str)`, `calculate_heartbeat_age_s(hb_ts)`, `format_ui_local_iso(ts_str)`
  - **Purpose:** Centralized time utility enforcing UTC ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`) for all database records and status files while providing local formatting for UI displays.

- **`worker_bundle.py`**
  - **Core Functions:** `_handle_event_bundle(...)`, `trigger_initial_ingestion(...)`, `_run_heartbeat(...)`
  - **Purpose:** Wires the worker's internal components together, launching `WatcherFS`, `WatcherProc`, the condition-variable `BurstAssembler`, and the heartbeat thread.

- **`worker_entry.py`**
  - **Classes:** `_WorkerEntry`
  - **Purpose:** Worker process entrypoint wrapper ensuring correct path injection, logger configuration, and uncaught exception handling.

- **`worker_helpers.py`**
  - **Classes:** `StopResult`
  - **Core Functions:** `_tokenize_cmdline(cmdline)`, `_extract_flag_value(tokens, flag)`, `validate_worker_process(pid, target_path, engine_root, worker_id)`, `verify_worker_pid(...)`, `stop_worker_process(...)`, `discover_active_workers(...)`
  - **Purpose:** Multi-attribute process verification engine preventing substring matching accidents by validating PID, creation time, executable, worker ID, and target path.

- **`worker_interlock.py`**
  - **Core Functions:** `_is_proc_alive(pid)`, `_terminate_proc(pid)`, `interlock_project_operation(project_name, op_type, target_path, engine_root)`
  - **Purpose:** Enforces single-operation mutual exclusion on project directories during concurrent lifecycle commands.

- **`worker_lifecycle.py`**
  - **Classes:** `WorkerState`
  - **Core Functions:** `_write_pid_file()`, `_remove_pid_file()`, `_write_status_file()`, `_clean_shutdown()`, `_crash_handler()`, `_install_signal_handlers()`
  - **Purpose:** Manages the worker process lifecycle: publishes `worker.pid` and `worker.status.json`, traps OS signals, and coordinates clean flushes on exit.

- **`worker_status.py`**
  - **Core Functions:** `safe_write_status_json(...)`, `read_status_file(...)`, `prune_phantom_sessions(...)`, `format_worker_status_row(...)`
  - **Purpose:** Thread-safe, atomic serialization and deserialization of worker telemetry records and status rows.
