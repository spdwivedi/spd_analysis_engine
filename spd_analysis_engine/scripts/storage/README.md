# Storage & Persistence Subsystem (`scripts/storage/`)

## Module Purpose
The `storage` module implements the persistence layer of the SPD Analysis Engine. It is responsible for single-writer SQLite transaction serialization, versioned schema migrations, workspace file manifest fingerprinting, multi-layered ignore policies, non-destructive rollbacks, and project directory layout management.

---

## File Roster & Technical Reference

- **`__init__.py`**
  - Package initialization and exports for storage components.

- **`db.py`**
  - **Classes:** `SessionDB`
  - **Core Functions:** `check_fault(name)`
  - **Purpose:** Thread-safe SQLite database manager for `session.db`. Provides unified query execution and transactional context managers.

- **`fingerprint.py`**
  - **Classes:** `FileFingerprint`, `ChangeResult`
  - **Core Functions:** `compute_fingerprint(path)`, `compare_fingerprints(before, after)`
  - **Purpose:** Computes lightweight file fingerprints (`exists`, `kind`, `size`, `sha256`) to evaluate effective changes (`before != after`). Correctly recognizes empty file creation, deletions, and binary edits without relying on textual diff strings.

- **`gitignore_manager.py`**
  - **Core Functions:** `suppress_file_path(path)`, `read_project_gitignore(root)`, `write_project_gitignore(root, entries)`
  - **Purpose:** Inspects and synchronizes `.gitignore` files to ensure engine internal artifacts are suppressed from primary source repositories.

- **`history.py`**
  - **Core Functions:** `_open_ro_connection(db_path)`, `_retry_cursor_execute(...)`, `consolidate_project_history(...)`
  - **Purpose:** Manages historical session rollups and exposes safe, read-only SQLite helper connections for history inspection.

- **`ignore_policy.py`**
  - **Classes:** `IgnorePolicy`
  - **Core Functions:** `_compile_glob_to_regex(pattern)`, `_parse_ignore_content(content)`
  - **Purpose:** Layered ignore evaluation engine. Combines built-in rules, `.gitignore`, `.spdignore`, and sensitive pattern blocklists. Exposes `should_watch()`, `should_diff()`, `should_stage()`, and `should_send_to_ai()`.

- **`manifest.py`**
  - **Classes:** `FileManifestEntry`, `FileManifest`
  - **Purpose:** High-performance in-memory workspace manifest tracking file sizes, nanosecond modification times, and SHA-256 digests for instant workspace state diffing.

- **`meta.py`**
  - **Core Functions:** `suppress_file_path()`, `_apply_meta_updates()`, `synthesize_project_meta()`, `read_project_meta(path, auto_synthesize=False)`, `update_project_meta()`, `resolve_project_context()`
  - **Purpose:** Reads and writes `project.meta` files. Strictly enforces `auto_synthesize=False` on ordinary read operations to eliminate read-induced storage mutation loops.

- **`migrator.py`**
  - **Classes:** `MigrationResult`, `ProjectMigrator`
  - **Purpose:** Idempotent migration pipeline for legacy storage tiers (`current/`, `last_run/`, `history/`) into permanent project storage directories.

- **`project_storage.py`**
  - **Core Functions:** `get_durable_root()`, `get_project_dir(slug)`, `ensure_project_layout(slug)`, `resolve_project_db(slug)`, `resolve_project_git(slug)`, `read_project_config(slug)`, `write_project_config(slug, config)`, `list_all_project_slugs()`
  - **Purpose:** Resolves physical and canonical storage paths for projects (`data/projects/<slug>/`).

- **`rollback.py`**
  - **Core Functions:** `check_fault(name)`, `_resolve_project_paths()`, `execute_non_destructive_rollback(...)`, `undo_rollback(...)`, `rollback_burst(...)`
  - **Purpose:** Executes safe, non-destructive rollbacks. Commits a pre-rollback snapshot, restores target files, marks affected historical events as `rolled_back` or `superseded`, and writes an auditable `ROLLBACK` event.

- **`rotator.py`**
  - **Classes:** `StorageRotator`
  - **Core Functions:** `rotate_project_to_history(project_name)`
  - **Purpose:** Rotates completed session directories into dated historical archives while preserving compatibility views.

- **`schema_migration.py`**
  - **Classes:** `MigrationReport`
  - **Core Functions:** `check_fault(name)`, `get_user_version(conn)`, `set_user_version(conn, ver)`, `_get_existing_columns(conn, table)`, `apply_schema_migrations(conn)`
  - **Purpose:** Executes versioned SQLite migrations using `PRAGMA user_version` (v1 through v5), ensuring forward-compatible additions to `events`, `patches`, and `sessions`.

- **`utils.py`**
  - **Core Functions:** `_load_time_utils()`, `_slug(name)`, `resolve_project_identity(name)`, `_remove_tree_force(path)`, `purge_test_artifacts()`
  - **Purpose:** Common utility helpers for slug generation, path cleaning, and identity mapping.

- **`writer.py`**
  - **Classes:** `ProjectWriter`
  - **Core Functions:** `check_fault(name)`
  - **Purpose:** Dedicated single-writer thread per project holding the exclusive write connection to `session.db`. Configured in WAL mode with `synchronous=NORMAL` and `busy_timeout=30000`. Exposes `open_ro_connection()` (`?mode=ro`) for safe concurrent readers.

- **`writer_ops.py`**
  - **Classes:** `WriteOperation`, `WriterMetrics`
  - **Core Functions:** `execute_operation(conn, op)`
  - **Purpose:** Encapsulates atomic write commands into FIFO queue items supporting `Future` synchronization for critical operations and tracking queue backpressure metrics.
