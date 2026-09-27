# scripts/storage/ — SQLite WAL Engine & Project Rotation

This sub-package handles all persistent state: the SQLite WAL event database, project metadata, three-tier directory rotation, history consolidation, and physical rollback.

---

## Module Map

| Module | Responsibility |
|---|---|
| [`db.py`](./db.py) | `SessionDB` class — full SQLite WAL schema and CRUD |
| [`meta.py`](./meta.py) | `read_project_meta()` / `update_project_meta()` — `project.meta` JSON |
| [`rotator.py`](./rotator.py) | `StorageRotator` — three-tier directory lifecycle |
| [`history.py`](./history.py) | `consolidate_project_history()` — merges last_run into history |
| [`rollback.py`](./rollback.py) | `rollback_burst()` — two-stage physical + DB rollback |
| [`utils.py`](./utils.py) | `_slug()`, `_utcnow_iso()`, `_remove_tree_force()`, `purge_test_artifacts()` |

---

## `SessionDB` (`db.py`)

The core database abstraction. Each project session has exactly one `SessionDB` instance pointing to `current/<slug>/session.db`.

### Schema

```sql
-- Events: one row per sealed burst or EXEC command
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL DEFAULT 1,
    event_type      TEXT    NOT NULL DEFAULT 'EDIT',  -- 'EDIT' | 'EXEC'
    first_file_touched TEXT,
    summary         TEXT,
    file_count      INTEGER DEFAULT 0,
    is_manual_seal  INTEGER DEFAULT 0,
    is_rolled_back  INTEGER DEFAULT 0,
    is_trashed      INTEGER DEFAULT 0,
    ai_summary      TEXT,                             -- JSON-encoded AI result
    created_at      TEXT    NOT NULL,
    burst_num       INTEGER,
    session_name    TEXT
);

-- Patches: unified diff content per file per event
CREATE TABLE IF NOT EXISTS patches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL REFERENCES events(id),
    file_path       TEXT    NOT NULL,
    diff_content    TEXT,
    snapshot_before TEXT,
    snapshot_after  TEXT,
    created_at      TEXT    NOT NULL
);

-- Sessions: metadata for each monitoring session
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT,
    started_at      TEXT    NOT NULL,
    ended_at        TEXT,
    interrupted     INTEGER DEFAULT 0,
    notes           TEXT
);
```

### Key Methods

```python
db = SessionDB(db_path)

# Record a sealed burst event
event_id = db.record_event(
    session_id=1,
    event_type="EDIT",
    first_file_touched="scripts/core/supervisor.py",
    summary="Modified debounce grace window calculation",
    file_count=3,
)

# Record a file patch (unified diff)
db.record_patch(
    event_id=event_id,
    file_path="scripts/core/supervisor.py",
    diff_content="@@ -120,3 +120,4 @@\n ...",
)

# Update AI analysis result
db.update_event_ai_summary(event_id, json.dumps(analysis_dict))

# Retrieve all events for the dashboard
events = db.get_events(session_id=1)

# WAL checkpoint (flush to main DB file)
db.checkpoint()
db.close()
```

### WAL Mode Configuration

On every connection open, `SessionDB` applies:

```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA foreign_keys=ON")
conn.execute("PRAGMA cache_size=-8192")   # 8 MB
conn.row_factory = sqlite3.Row            # dict-like row access
```

---

## `StorageRotator` (`rotator.py`)

Manages the three-tier storage lifecycle for each project.

### Tier Architecture

```
engine_root/
├── current/
│   └── <slug>/          ← LIVE session (worker active)
│       ├── session.db
│       ├── project.meta
│       └── shadow_git/
│
├── last_run/
│   └── <slug>/          ← Most recent completed session
│       ├── session.db
│       └── project.meta
│
└── history/
    └── <slug>/
        ├── run_20260927_143000/   ← Archived sessions
        ├── run_20260926_091500/
        └── ...
```

### Rotation Flow

**On `stop_project()`:**
```
current/<slug>/ → last_run/<slug>/
```
If `last_run/<slug>/` already exists, it is first consolidated to `history/<slug>/run_<ts>/`.

**On `resume_project()`:**
```
last_run/<slug>/ → current/<slug>/
```

**On `archive_project()` (called by stop):**
1. Move `last_run/<slug>/` to `history/<slug>/run_<timestamp>/`.
2. Move `current/<slug>/` to `last_run/<slug>/`.

---

## `project.meta` Schema (`meta.py`)

Each project's configuration is stored in `current/<slug>/project.meta` as JSON.

```json
{
  "slug": "nova_pulse",
  "project_name": "nova_pulse",
  "target_path": "D:\\Projects\\nova_pulse\\src",
  "debounce_window": 300.0,
  "monitored_ai_env": "Google Antigravity",
  "worker_pid": 12345,
  "status": "running",
  "started_at": "2026-09-27T23:00:00+05:30",
  "last_heartbeat_at": "2026-09-27T23:01:00+05:30",
  "powers": {
    "ai_enabled": true,
    "git_sync_enabled": false,
    "trash_enabled": true
  }
}
```

### Key Rule: Never Overwrite Custom Debounce Values

`update_project_meta()` only updates fields that are **explicitly provided**. If `debounce_window` is not in the `kwargs`, the existing value is preserved.

```python
def update_project_meta(engine_root, project_name, **kwargs):
    existing = read_project_meta(engine_root, project_name)
    # Merge: only overwrite fields present in kwargs
    for key, val in kwargs.items():
        existing[key] = val
    # Write back
    meta_path.write_text(json.dumps(existing, indent=2))
```

---

## History Consolidation (`history.py`)

`consolidate_project_history()` merges the SQLite events from multiple past sessions into a unified view without copying the raw files. It reads event records chronologically across all `history/<slug>/run_*/session.db` files and produces a merged event stream for the dashboard's historical view.

---

## Rollback (`rollback.py`)

`rollback_burst()` orchestrates the two-stage physical + DB rollback. See [`notes/04_PHYSICAL_ROLLBACK.md`](../../notes/04_PHYSICAL_ROLLBACK.md) for the full runbook.

---

## Utilities (`utils.py`)

```python
# Normalize project name to a filesystem-safe slug
_slug("My Project!") → "my_project_"

# Current UTC timestamp in ISO 8601
_utcnow_iso() → "2026-09-27T17:30:00+00:00"

# Windows-hardened recursive directory removal
_remove_tree_force(path: Path) → None

# Clean up test artifact directories
purge_test_artifacts(engine_root: Path) → None
```
