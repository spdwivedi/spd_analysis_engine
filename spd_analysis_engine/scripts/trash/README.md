# Trash & Safe Deletion Subsystem (`scripts/trash/`)

## Module Purpose
The `trash` module provides safe, reversible deletion and purge mechanics for project workspaces, individual burst records, and session archives. It ensures that user data is never destroyed inadvertently by routing deletions through a staged soft-delete trash store with full recovery capabilities.

---

## File Roster & Technical Reference

- **`__init__.py`**
  - Package initialization and exports for trash managers and operations.

- **`burst_trash.py`**
  - **Core Functions:** `delete_selective_bursts(db_path, burst_ids)`
  - **Purpose:** Enables selective soft-deletion and purging of erroneous or unwanted individual burst records from `session.db` and Shadow Git.

- **`constants.py`**
  - **Core Functions:** `_timestamp_prefix()`
  - **Purpose:** Defines default directory paths, naming conventions, and timestamp prefixes for trash items.

- **`fs_ops.py`**
  - **Core Functions:** `_strip_readonly_recursive(path)`, `_safe_rmtree(path)`, `_safe_move_tree(src, dst)`, `_parse_orphan_trash_dir(dir_name)`
  - **Purpose:** Robust filesystem manipulation routines designed for Windows permission models (handles read-only attributes and file locks cleanly).

- **`manager.py`**
  - **Classes:** `TrashManager`
  - **Purpose:** High-level coordinator managing project soft-deletion, restoration, listing, and permanent purging.

- **`project_trash.py`**
  - **Core Functions:** `delete_metadata_only(...)`, `delete_local_session(...)`, `delete_session_and_shadow_git(...)`, `delete_complete_erase(...)`
  - **Purpose:** Granular deletion levels allowing users to remove metadata only, remove local sessions while preserving history, or execute total erasures.

- **`purge.py`**
  - **Core Functions:** `list_trash_entries(trash_dir)`, `purge_trash_entry(entry_id)`, `purge_all_trash(trash_dir)`
  - **Purpose:** Permanent removal routines for aged trash items.

- **`remote_gh.py`**
  - **Core Functions:** `delete_remote_github_repo(repo_name, token)`
  - **Purpose:** Handles optional deletion of remote mirror repositories when complete erasure is requested.

- **`restore.py`**
  - **Core Functions:** `restore_from_trash(entry_id, destination_dir)`
  - **Purpose:** Restores previously trashed project sessions back to their original runtime location.
