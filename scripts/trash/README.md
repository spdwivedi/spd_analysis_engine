# scripts/trash/ — Soft-Delete System

This sub-package implements the soft-delete system for burst events, sessions, and project data. It is designed to be Windows-safe, using read-only flag stripping and retry loops to handle the `WinError 5` and `WinError 32` permission errors common on Windows.

---

## Module Map

| Module | Responsibility |
|---|---|
| [`manager.py`](./manager.py) | `TrashManager` — central orchestrator for all trash operations |
| [`fs_ops.py`](./fs_ops.py) | `_force_rmtree()`, `_remove_readonly_recursive()`, `_on_rm_error()` |
| [`burst_trash.py`](./burst_trash.py) | `delete_selective_bursts()` — move specific events to trash |
| [`purge.py`](./purge.py) | `list_trash_entries()`, `purge_trash_entry()`, `purge_all_trash()` |
| [`restore.py`](./restore.py) | `restore_trash_entry()` — recover items from trash |
| [`remote_gh.py`](./remote_gh.py) | Remote GitHub burst deletion (for synced milestone repos) |
| [`constants.py`](./constants.py) | Shared constants: `TRASH_DIR`, `TRASH_META_FILE`, max sizes |

---

## Design Philosophy

The trash system follows a **soft-delete-first** philosophy:

1. Nothing is permanently deleted immediately.
2. All deleted items are serialized to JSON and moved to `trash/<slug>/`.
3. Items can be **restored** or **permanently purged** from the trash panel in the dashboard.
4. Physical disk cleanup happens only during explicit `purge` operations.

This ensures that accidental deletions are always recoverable within the current session.

---

## `TrashManager` (`manager.py`)

The central orchestrator. The HTTP server holds one `TrashManager` instance shared across all requests.

### Key Methods

```python
tm = TrashManager(engine_root=Path("."))

# List all trash entries for a project
entries = tm.list_trash_entries(project_name="nova_pulse")
# Returns: [{"id": "...", "type": "burst", "event_id": 42, "trashed_at": "..."}]

# Restore a trash entry back to the session DB
result = tm.restore_trash_entry(entry_id="trash_abc123", project_name="nova_pulse")

# Permanently delete a specific trash entry
tm.purge_trash_entry(entry_id="trash_abc123", project_name="nova_pulse")

# Permanently delete all trash for a project
tm.purge_all_trash(project_name="nova_pulse")
```

### Worker Stop Integration

When a worker stops, `TrashManager._stop_active_worker_if_running(slug)` is called to ensure any ongoing deletion operations are completed before the session is archived.

---

## Windows-Hardened File Operations (`fs_ops.py`)

### The Core Problem

On Windows, files in `.git/objects/` (including the shadow git) have the `FILE_ATTRIBUTE_READONLY` bit set by Git. Standard `shutil.rmtree()` raises:

```
PermissionError: [WinError 5] Access is denied: '...shadow_git\objects\8a\afd3...'
```

### Solution: Two-Layer Hardening

**Layer 1:** Strip the read-only attribute recursively before attempting deletion.

```python
def _remove_readonly_recursive(path: Path) -> None:
    for root, dirs, files in os.walk(str(path)):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                os.chmod(fpath, stat.S_IWRITE)
            except Exception:
                pass  # Best-effort; continue
```

**Layer 2:** Error handler that retries after chmod on a per-file basis.

```python
def _on_rm_error(func, path, exc_info):
    """onerror/onexc handler for shutil.rmtree."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)  # Retry the deletion
    except Exception:
        pass  # Best-effort
```

**Full `_force_rmtree()` implementation:**

```python
def _force_rmtree(path: Path | str) -> None:
    path = Path(path)
    if not path.exists():
        return

    # Pre-strip all read-only bits
    _remove_readonly_recursive(path)

    try:
        # Python 3.12+: onexc parameter
        shutil.rmtree(str(path), onexc=_on_rm_error)
    except TypeError:
        # Python < 3.12: onerror parameter (fallback)
        shutil.rmtree(str(path), onerror=_on_rm_error)
```

### Handling WinError 32 (File In Use)

When a file is locked by another process (e.g. Windows Defender, another Python process), `_force_rmtree` uses a retry loop:

```python
for attempt in range(5):
    try:
        shutil.move(str(source), str(dest))
        break
    except PermissionError as e:
        if attempt == 4:
            raise  # Give up after 5 attempts
        time.sleep(0.2)  # Wait 200ms before retry
```

---

## Selective Burst Deletion (`burst_trash.py`)

`delete_selective_bursts()` allows deleting individual burst events without a full rollback.

```python
result = delete_selective_bursts(
    project_name="nova_pulse",
    event_ids=[38, 39, 40],
    engine_root=Path("."),
)
```

### What Happens

1. For each `event_id`:
   - Read the event and its patches from `session.db`.
   - Serialize to a JSON trash file in `trash/<slug>/event_<id>_<ts>.json`.
   - Set `is_trashed=1` in the events table (soft delete).
   - Delete the raw diff content from the patches table (reclaims disk space).
2. Return a list of created trash file paths.

The trashed events are no longer visible in the timeline but remain in `trash/<slug>/` for potential restoration.

---

## Purge Operations (`purge.py`)

### `list_trash_entries(project_name)`

Returns a structured list of all items in the trash for a project:

```python
entries = list_trash_entries("nova_pulse")
# [
#   {
#     "id": "event_42_1727459200",
#     "type": "burst",
#     "event_id": 42,
#     "file_path": "trash/nova_pulse/event_42_1727459200.json",
#     "trashed_at": "2026-09-27T23:00:00+05:30",
#     "size_bytes": 4096,
#   },
#   ...
# ]
```

### `purge_trash_entry(entry_id, project_name)`

Permanently deletes a single trash JSON file from disk. This operation is **irreversible**.

### `purge_all_trash(project_name)`

Uses `_force_rmtree()` to permanently delete the entire `trash/<slug>/` directory.

---

## Restore Operations (`restore.py`)

`restore_trash_entry()` reads the trash JSON file and re-inserts the event and patches back into `session.db`:

```python
result = restore_trash_entry(
    entry_id="event_42_1727459200",
    project_name="nova_pulse",
    engine_root=Path("."),
)
# Returns: {"success": True, "restored_event_id": 42}
```

After restoration:
- `is_trashed` is set to `0` in the events table.
- The patches are re-inserted.
- The trash JSON file is deleted.
- The timeline card reappears in the dashboard.

---

## Trash Constants (`constants.py`)

```python
TRASH_DIR = "trash"                    # Root trash directory name
TRASH_META_FILE = "trash_index.json"  # Per-project trash index
MAX_TRASH_ENTRIES = 500               # Max items per project before auto-purge warning
MAX_TRASH_SIZE_MB = 512               # Soft limit for trash disk usage warning
```
