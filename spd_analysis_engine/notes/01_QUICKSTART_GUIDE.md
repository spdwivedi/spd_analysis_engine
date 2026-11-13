# Quickstart Guide — SPD Analysis Engine

> **Target audience:** Developer setting up the engine for the first time or returning to a paused session.

---

## 1. Prerequisites Checklist

Before launching, verify the following:

```powershell
# Check Python version (must be 3.12+)
python --version

# Check Git is on PATH (required for Shadow Git)
git --version

# (Optional) Check Ollama is running
curl http://127.0.0.1:11434/api/tags
```

---

## 2. First-Time Setup

```bash
# Clone and enter the workspace
git clone https://github.com/spdwivedi/spd_analysis_engine.git
cd spd_analysis_engine/spd_analysis_engine

# Create a virtual environment
python -m venv .venv

# Activate (Windows PowerShell)
.venv\Scripts\Activate.ps1

# Activate (Linux / macOS bash)
source .venv/bin/activate

# Install all dependencies
pip install -r requirements.txt

# Copy example configs and fill in your credentials
cp ai_data/models_config.example.json ai_data/models_config.json
cp ai_data/git_config.example.json    ai_data/git_config.json
```

Edit `ai_data/models_config.json` to add your Gemini API key or Ollama configuration.
See [`02_SECRETS_AND_CONFIG.md`](./02_SECRETS_AND_CONFIG.md) for detailed instructions.

---

## 3. Launching the Engine

### Standard Launch (with Dashboard UI)

```bash
python start.py ui --port 8765
```

This starts:
1. The native HTTP server on `localhost:8765`
2. The Supervisor process manager
3. The SSE event loop
4. Opens your browser to `http://localhost:8765`

### Headless Launch (no browser auto-open)

```bash
python start.py --headless --port 8765
```

### Custom Port

```bash
python start.py ui --port 9000
```

---

## 4. Project Management via the Dashboard

### Starting a Project

1. Click **"+ Add Project"** in the sidebar.
2. Enter a **project name** (e.g., `nova_pulse`).
3. Enter the **target directory path** to monitor (e.g., `D:\Projects\nova_pulse\src`).
4. Optionally set:
   - **Debounce Window** (seconds): how long of a quiet period defines a burst boundary (default: 300s).
   - **AI Environment**: label for the AI context (e.g., `Google Antigravity`).
5. Click **Start Monitoring**.

### Stopping a Project

Click the **Stop** button next to the project. The engine will:
1. Seal the current open burst.
2. Archive the session to `last_run/<slug>/`.
3. Consolidate history if a prior `last_run` exists.

### Resuming a Project

Click **Resume** on a stopped project. The engine restores:
- The original `target_path`
- The `debounce_window` setting
- All prior `powers` flags (AI, Git, Trash)

---

## 5. CLI Commands Reference

All CLI commands are run from the `spd_analysis_engine/` directory with your virtual environment activated.

### Engine Management

```bash
# Start the engine (UI mode)
python start.py ui --port 8765

# Start headless
python start.py --headless --port 8765

# Run the compliance audit (PowerShell)
powershell -ExecutionPolicy Bypass -File .\audit_engine.ps1
```

### API Interaction (curl / PowerShell)

```powershell
# Check engine status
Invoke-RestMethod http://localhost:8765/api/status

# List all projects
Invoke-RestMethod http://localhost:8765/api/projects

# Start monitoring a new project
$body = @{
    project_name   = "nova_pulse"
    target_path    = "D:\Projects\nova_pulse\src"
    debounce_window = 300
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri http://localhost:8765/api/project/start `
    -Body $body -ContentType "application/json"

# Stop a project
Invoke-RestMethod -Method POST -Uri http://localhost:8765/api/project/nova_pulse/stop

# Manually seal the current burst (Lap)
Invoke-RestMethod -Method POST -Uri http://localhost:8765/api/project/nova_pulse/seal-burst `
    -Body '{}' -ContentType "application/json"

# Get events for a project
Invoke-RestMethod http://localhost:8765/api/project/nova_pulse/events

# Trigger AI analysis of a burst
Invoke-RestMethod -Method POST `
    -Uri http://localhost:8765/api/project/nova_pulse/analyze-event/42 `
    -Body '{}' -ContentType "application/json"

# Export session as JSON
Invoke-RestMethod http://localhost:8765/api/project/nova_pulse/export
```

---

## 6. Running the Compliance Audit

The `audit_engine.ps1` script scans every `.py` file in `scripts/` and reports any file exceeding 500 lines (the modularization compliance limit).

```powershell
# From the spd_analysis_engine/ directory
powershell -ExecutionPolicy Bypass -File .\audit_engine.ps1
```

Expected output when compliant:
```
[AUDIT] Scanning scripts/ for Python files over 500 lines...
[OK] All 70+ files are within the 500-line limit.
[AUDIT PASSED] 0 violations found.
```

---

## 7. Troubleshooting

### Port Already in Use

**Error:** `OSError: [Errno 98] Address already in use` (Linux) or `WinError 10048` (Windows)

**Fix:**
```powershell
# Windows: find and kill the process on port 8765
netstat -ano | findstr :8765
taskkill /PID <pid> /F
```

```bash
# Linux: find and kill the process on port 8765
fuser -k 8765/tcp
```

Then re-launch with `python start.py ui --port 8765`.

### Zombie Worker Processes

If the engine crashes mid-session, orphan worker processes may linger. The Reaper (`scripts/core/reaper.py`) handles cleanup on the next start, but you can force-clean manually:

```powershell
# Windows: kill all python processes (caution: this kills ALL python processes)
Get-Process python | Stop-Process -Force

# Or target just spd workers
Get-Process python | Where-Object { $_.CommandLine -like "*project_worker*" } | Stop-Process -Force
```

### Shadow Git Lock File

**Error:** `fatal: Unable to create '...shadow_git/.git/index.lock': File exists.`

**Fix:**
```bash
# Delete the lock file
rm current/<project_slug>/shadow_git/.git/index.lock
```

### Ollama Connection Refused

**Error:** `WinError 10061: No connection could be made because the target machine actively refused it`

This is **expected and non-fatal** when Ollama is not running. The engine falls back to heuristic analysis automatically. To enable Ollama:

```bash
# Install Ollama from https://ollama.ai
ollama serve
ollama pull qwen2.5-coder:7b
```

### SQLite WAL Database Corruption

If the database is corrupted (rare), you can attempt recovery:

```bash
python -c "
import sqlite3
conn = sqlite3.connect('current/my_project/session.db')
conn.execute('PRAGMA integrity_check')
conn.execute('PRAGMA wal_checkpoint(FULL)')
conn.close()
print('WAL checkpoint complete')
"
```

### Process Reconciliation on Resume

When resuming a project after an unexpected shutdown (e.g. system reboot), the Supervisor checks:
1. Whether the worker PID from `project.meta` is still alive.
2. If the PID is dead, it marks the session as `CRASHED` in the database.
3. It then starts a fresh worker and opens a new session.

The dashboard shows `"Session was terminated unexpectedly"` as a toast notification, which is expected behavior for crash recovery.

---

## 8. Data Locations

| Data | Path |
|---|---|
| Live session DB | `current/<slug>/session.db` |
| Shadow Git repo | `current/<slug>/shadow_git/` |
| Project metadata | `current/<slug>/project.meta` |
| Archived sessions | `last_run/<slug>/` and `history/<slug>/run_<ts>/` |
| AI cache | `ai_data/cache/<slug>/` |
| AI config | `ai_data/models_config.json` |
| Git config | `ai_data/git_config.json` |
| Soft-deleted items | `trash/` |
