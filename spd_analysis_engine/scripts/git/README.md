# Shadow Git Subsystem (`scripts/git/`)

## Module Purpose
The `git` module manages isolated micro-versioning for monitored developer workspaces. It maintains a dedicated Shadow Git repository (`shadow_git/`) separate from the user's primary `.git`, executes atomic burst commits, handles pathspec batching for large file volumes (1,000+ files), tracks milestone pushes, and executes non-destructive rollbacks.

---

## File Roster & Technical Reference

- **`__init__.py`**
  - Package initialization and exports for Shadow Git classes and utilities.

- **`auto_pusher.py`**
  - **Core Functions:** `is_repo_in_merge_or_rebase(repo_dir)`, `is_index_locked(repo_dir)`, `scrub_sensitive_output(text)`, `create_askpass_context(...)`, `stage_milestone_files_only(...)`, `execute_auto_push(...)`, `handle_post_seal_autopush(...)`
  - **Purpose:** Manages optional remote repository auto-push jobs. Enforces safety guards (refuses push during merges, rebases, or index locks) and runs via isolated background jobs using `GIT_ASKPASS`.

- **`credentials.py`**
  - **Classes:** `GitCredentialManager`
  - **Purpose:** Securely manages Git authentication credentials without persisting tokens in repository URLs or command lines.

- **`milestone_tracker.py`**
  - **Core Functions:** `get_unpushed_bursts(db_path)`, `record_git_milestone(db_path, ...)`, `get_project_milestone_history(db_path)`
  - **Purpose:** Tracks cumulative burst progress and records milestone markers in the project database.

- **`remote_sync.py`**
  - **Core Functions:** `push_to_remote(repo_path, remote, branch)`
  - **Purpose:** Low-level Git push wrapper supporting explicit branch synchronization.

- **`shadow_commit.py`**
  - **Classes:** `GitState`, `GitBurstResult`
  - **Core Functions:** `get_shadow_git_env()`, `configure_shadow_repository(repo_dir)`, `stage_paths_nul(repo_dir, paths)`, `execute_burst_commit(...)`, `record_git_op_state(...)`, `recover_unfinished_git_ops(...)`
  - **Purpose:** Core burst commit engine. Configures Git optimizations (`core.longpaths=true`, `gc.auto=0`), executes atomic burst commits without `--allow-empty`, handles large path lists via NUL-delimited staging, and persists Git retry state in SQLite.

- **`shadow_push.py`**
  - **Core Functions:** `push_to_remote_repo(...)`
  - **Purpose:** Pushes shadow repository history to secondary mirror remotes if configured.

- **`shadow_repo.py`**
  - **Classes:** `ShadowGit`
  - **Purpose:** Main Shadow Git interface class coordinating repository initialization, burst commits, patch diff extractions, and branch status queries.

- **`shadow_rollback.py`**
  - **Classes:** `ShadowRollbackMixin`
  - **Core Functions:** `rollback_to_burst(...)`, `commit_snapshot(...)`, `compute_rollback_plan(...)`, `restore_files_non_destructive(...)`
  - **Purpose:** Git-level operations for non-destructive rollbacks, creating pre-rollback snapshot commits and checking out specific historical trees without executing `git clean -fd`.

- **`utils.py`**
  - **Core Functions:** `ensure_hardened_gitignore(repo_dir)`, `scrub_tokens(cmd_str)`, `discover_native_git_remote(project_path)`, `is_salesforce_or_transient_file(rel_path)`
  - **Purpose:** Git environment utilities, remote URL discovery, and token scrubbing from Git command outputs.
