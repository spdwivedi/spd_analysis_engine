# Telemetry Engine Deep-Dive

> How the SPD Analysis Engine captures, debounces, stores, and micro-versions your every code edit—with less than 0.15 ms of overhead.

---

## 1. The Telemetry Pipeline

```
IDE saves file
      │
      ▼
Filesystem Watcher (ReadDirectoryChangesW / inotify)
      │  raw change event
      ▼
Event Bundler  ← debounce_window timer reset on every new change
      │  sealed burst (after quiet period)
      ▼
Project Worker
      ├──► SessionDB.record_event()      → SQLite WAL
      ├──► SessionDB.record_patch()      → unified diff stored
      ├──► ShadowGit.commit_burst()      → micro-commit in shadow_git/
      └──► SSE push to dashboard         → live card appears
```

---

## 2. The Debounce Sliding Window Algorithm

The core behavioral contract of the engine is: **a "burst" is a cluster of file edits separated from the next cluster by a quiet window.**

### Algorithm (Monotonic Countdown)

```
         Edit    Edit     Edit              Edit  Edit
           │       │        │                │     │
───────────●───────●────────●────────────────●─────●──────────
           │       │        │                │     │
           └──▶ timer       └──▶ timer reset  └──▶ timer reset
               reset              │
                                  │  ← quiet window: no edits
                                  ▼
                            ┌───────────┐
                            │  BURST    │  ← sealed, committed to DB
                            │  SEALED   │     + shadow git commit
                            └───────────┘
```

**Properties:**
- The timer is a **monotonic countdown** using `time.monotonic()` — immune to system clock adjustments.
- Every new filesystem change **resets the countdown** to `debounce_window` seconds (default: 300s).
- When the countdown expires with no new changes, the burst is **sealed**.
- A sealed burst is atomic: all patches between the last seal and this seal belong to exactly one burst.

### Configurable Thresholds

| Parameter | Default | Description |
|---|---|---|
| `debounce_window` | 300s (5 min) | Quiet period before a burst is sealed |
| `hb_grace_threshold` | `max(debounce_window, 12.0)s` | Heartbeat grace window for dead-worker detection |

The `hb_grace_threshold` is automatically computed as `max(debounce_window, 12.0)` so that a large debounce window (e.g. 600s) doesn't trigger false-positive dead-worker detection during a long quiet edit session.

### Jitter Suppression

The bundler suppresses noise events caused by:
- **Build artifacts:** `*.pyc`, `__pycache__/`, `node_modules/`, `.sf/`, `.sfdx/`
- **Transient files:** `.tmp`, `.lock`, `.swp`, Git index lock files
- **Salesforce DX patterns:** `.forceignore`-pattern files

The `is_sanitized_patch()` function in `scripts/ai/prompt_builder.py` determines whether a file's diff is meaningful enough to send to AI synthesis.

---

## 3. Manual Burst Sealing — The Lap Button

The dashboard provides a **⚡ Seal Burst Now (Lap)** button that forces an immediate burst seal regardless of the debounce countdown.

**Use cases:**
- Before switching to a different feature mid-session
- After completing a logical unit of work you want clearly separated in the timeline
- Before triggering a manual AI analysis on a clean boundary

**API equivalent:**
```bash
POST /api/project/<name>/seal-burst
Body: {}
```

**What happens internally:**
1. The bundler's countdown is immediately cancelled.
2. All buffered file changes are flushed as a complete burst.
3. `SessionDB.record_event()` creates a new event record with `is_manual_seal=True`.
4. `ShadowGit.commit_burst()` creates a tagged micro-commit with `[LAP]` in the message.
5. The SSE stream pushes the sealed burst to all connected dashboard clients.

---

## 4. Shadow Git Micro-Versioning

The Shadow Git system creates a **completely isolated `.git` repository** inside `current/<slug>/shadow_git/` that is separate from the developer's own project `.git`.

### Architecture

```
current/
  nova_pulse/
    session.db          ← SQLite WAL (events, patches, AI summaries)
    project.meta        ← JSON project config (slug, debounce_window, etc.)
    shadow_git/         ← Isolated micro-versioning repo
      .git/             ← Shadow Git directory
      <mirrored files>  ← Workspace files checked out at each burst
```

### Commit Lifecycle

For each sealed burst:

```python
# From scripts/git/shadow_repo.py
sg = ShadowGit(shadow_dir=shadow_git_path, target_dir=workspace_path)

# Stage all changes (using --work-tree to point at the real workspace)
sg._run_git(["add", "-A"])

# Create a micro-commit
commit_hash = sg.commit_burst(
    event_id=42,
    summary="Modified core/supervisor.py — debounce grace window fix",
    files_changed=["scripts/core/supervisor.py"],
)
```

### Unified Diff Delta Extraction

The Shadow Git also provides **delta diffs between any two burst commits**:

```python
# Get the diff between burst #41 and burst #42
delta = sg.get_burst_delta_diff(event_id=42, files=["scripts/core/supervisor.py"])
```

This uses `git diff <hash_A> <hash_B> -- <file>` with the shadow repo's history, giving a clean unified diff that excludes IDE noise.

### Git Configuration for Shadow Repo

The shadow repo uses a hardened `.gitignore` template (`HARDENED_GITIGNORE_TEMPLATE` in `scripts/git/utils.py`) that excludes:
- `.sf/`, `.sfdx/`, `node_modules/`, `__pycache__/`, `.venv/`
- Build artifacts: `*.pyc`, `*.class`, `*.o`, `dist/`, `build/`
- IDE configs: `.vscode/settings.json`, `.idea/`

---

## 5. Heartbeat & Worker Health Monitoring

### Heartbeat Protocol

Each project worker updates a `last_heartbeat_at` timestamp in its `project.meta` file every **N seconds** (where N ≤ `hb_grace_threshold / 2`).

The Supervisor polls `project.meta` for each active project. If `now - last_heartbeat_at > hb_grace_threshold`, the worker is considered dead.

### Dead Worker Response

1. **Reaper** (`scripts/core/reaper.py`) attempts `SIGTERM` then `SIGKILL` on the PID.
2. The session is marked as `CRASHED` in the database.
3. On the next `resume`, a fresh worker starts a new session with `SessionDB.create_session(interrupted=True)`.
4. The dashboard shows a toast: `"Session was terminated unexpectedly (e.g. system reboot, task kill, power outage)."`

### OS PID Validation

Before auto-archiving a project marked as inactive, the Supervisor performs an OS-level PID validation:
```python
import psutil
if psutil.pid_exists(meta.get("worker_pid")) and \
   any("project_worker" in p.name() for p in psutil.process_iter()):
    # Worker is actually alive — don't archive
```
This prevents false positives on Linux where PIDs can be reused by unrelated processes.

---

## 6. SSE Event Stream Format

The dashboard subscribes to a Server-Sent Events stream at `/api/events/stream`.

### Event Types

| Event Type | When Emitted | Payload |
|---|---|---|
| `heartbeat` | Every 5 seconds | `{"ts": "...", "ram_mb": 45.2}` |
| `burst_sealed` | When a burst is committed | Full event dict + AI summary |
| `exec_event` | When a shell command is captured | Command, exit code, duration |
| `project_started` | When monitoring begins | Project name, target path |
| `project_stopped` | When monitoring ends | Project name, timestamp |
| `ai_analysis_ready` | When async AI batch completes | Event ID, analysis dict |
| `rollback_started` | When rollback begins | Burst ID, target event ID |
| `rollback_complete` | When rollback finishes | Success flag, reverted files |

### SSE Wire Format

```
data: {"event_type": "burst_sealed", "project": "nova_pulse", "event_id": 42, ...}

data: {"event_type": "heartbeat", "ts": "2026-09-27T23:00:00+05:30", "ram_mb": 45.2}
```

Each message is a single JSON-encoded line prefixed with `data: `.

---

## 7. EXEC Event Capture (Shell Interception)

The engine monitors shell command execution through `scripts/shell_interceptor.py` and the `scripts/shell/` package.

### What Gets Captured

- Terminal commands run while the project is monitored: `npm test`, `python -m pytest`, `git commit`, `make build`
- Command duration in seconds
- Exit code (0 = success, non-zero = failure)
- Whether the command is considered "significant" by the classifier

### Classifier Logic (`scripts/shell/classifier.py`)

Commands are classified into categories:
- `TEST_RUN` — `pytest`, `npm test`, `jest`, `cargo test`
- `BUILD` — `make`, `npm run build`, `cargo build`
- `GIT_OP` — `git commit`, `git push`, `git merge`
- `INSTALL` — `pip install`, `npm install`, `yarn add`
- `RUN` — `python <script>`, `node <script>`
- `IGNORED` — `ls`, `cd`, `echo`, `cat` (not recorded)

EXEC events appear in the timeline alongside EDIT bursts, giving a full narrative of your development session.
