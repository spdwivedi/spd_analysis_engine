# Shell & Terminal Telemetry Subsystem (`scripts/shell/`)

## Module Purpose
The `shell` module passively observes terminal command executions, shell activities, and file access patterns initiated by developer processes. It correlates shell commands with filesystem edits, identifies active compiler and test runners, and scrubs credentials from recorded command strings.

---

## File Roster & Technical Reference

- **`__init__.py`**
  - Package initialization and exports for shell tracking components.

- **`classifier.py`**
  - **Core Functions:** `is_ignored_command(cmd_str)`, `detect_executor(cmd_str)`
  - **Purpose:** Classifies shell commands into categories (compiler, test runner, git operation, utility) and ignores high-frequency non-development commands.

- **`cmd_parser.py`**
  - **Core Functions:** `scrub_tokens(cmd_str)`, `_utcnow_iso()`, `_is_path_inside_target(path, target)`, `_detect_file_reads_from_cmd(cmd_tokens, target)`
  - **Purpose:** Parses command lines to detect referenced workspace files and strips sensitive tokens (passwords, auth flags, bearer tokens) before storage.

- **`tracker.py`**
  - **Classes:** `ExecutionTracker`
  - **Purpose:** Main execution tracker polling active shell sessions, recording command telemetry into `session.db`, and linking executions to adjacent burst events.
