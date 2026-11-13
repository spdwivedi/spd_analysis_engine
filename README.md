# SPD Analysis Engine

**Enterprise-Grade, Local-First Developer Telemetry & Shadow Git Micro-Versioning Engine**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows_%7C_Linux_%7C_macOS-lightgrey)](.)
[![Architecture: SDD Sidecar](https://img.shields.io/badge/Architecture-Sidecar_Control_Plane-purple)](./Plan/)
[![Privacy: Local-First](https://img.shields.io/badge/Privacy-Local--First-brightgreen)](.)

---

## 1. Project Overview

The **SPD Analysis Engine** is an asynchronous, local-first monitoring daemon and micro-versioning platform designed for modern software development environments. Operating quietly on developer workstations, it observes filesystem activity across active workspaces, coalesces rapid editing bursts into discrete change sets using condition-variable debouncing, commits unified diffs to an isolated Shadow Git repository, records file and process telemetry into a high-concurrency SQLite WAL database, and serves a live, reactive web control portal.

### Key Architectural Pillars
- **Zero Editor Interference:** Operates completely out-of-band via detached OS background worker processes.
- **Micro-Versioning Without Primary Git Pollution:** Shadow Git tracks workspace history in an isolated `.git` shadow tree, enabling non-destructive rollback without affecting developer branches, working trees, or commits.
- **Single-Writer Concurrency:** Dedicated `ProjectWriter` threads serialize SQLite writes in WAL mode, guaranteeing zero `database locked` operational errors while permitting unbounded concurrent read queries (`?mode=ro`).
- **Condition-Variable Burst Assembly:** Zero `threading.Timer` instances; dynamic sliding window debouncing (`due = min(last + quiet, first + max)`) guarantees timely capture under continuous editing.
- **Decoupled Controller AI:** Workers operate without cloud API keys. The central controller schedules durable AI analysis jobs (L1 Quick through L4 Risk) with deterministic pre-flight secret sanitization.

---

## 2. "What is Where" (Directory Routing)

The repository implements a strict **Sidecar Governance & Runtime Architecture** separating specification, build artifacts, and live application code:

```text
D:\Project_Files\SPD_Analysis_Engine\
├── Plan/                               # [GOVERNANCE PLANE]
│   │                                   # Holds all architecture specs, ADRs, and roadmaps
│   ├── 00_START_HERE.md                # System invariants & architectural boundary rules
│   ├── ARCHITECTURE_CURRENT.md         # Production architecture & data flow specification
│   ├── CURRENT_STATE.md                # Real-time lifecycle state & active pointers
│   ├── DECISIONS.md                    # Locked Architectural Decision Records (ADRs)
│   ├── GLOBAL_ROADMAP.md               # Master roadmap (Historical completed + Stage 09 future)
│   ├── Reference/History/              # Permanent raw architectural audits (2026 Audit)
│   └── Stages/                         # Granular execution specifications (S00 - S08)
│
├── artifacts/                          # [ARTIFACTS STORE]
│   │                                   # Holds generated PDFs, formal architecture exports,
│   │                                   # audit bundles, and compliance reports.
│   └── (Generated PDF reports & architecture bundles)
│
├── spd_analysis_engine/                # [RUNTIME APPLICATION PLANE]
│   │                                   # Production runtime application, scripts, and tests
│   ├── start.py                        # Master CLI & Web Portal entry point
│   ├── scripts/                        # Modular engine implementation
│   │   ├── core/                       # Spawner, supervisor, BurstAssembler, worker bundles
│   │   ├── controller/                 # LifecycleController, Reconciler, engine.db registry
│   │   ├── storage/                    # ProjectWriter, schema migrations, manifest, rollback
│   │   ├── server/                     # stdlib HTTP daemon, SSE bus, declarative REST API
│   │   ├── ai/                         # ControllerAIExecutor, provider pools, secret sanitizer
│   │   ├── git/                        # Shadow Git micro-commit engine, auto-push jobs
│   │   ├── shell/                      # Terminal command & process execution tracking
│   │   └── trash/                      # Safe soft-delete and restore subsystem
│   ├── web/                            # Zero-dependency vanilla ES6 reactive dashboard
│   ├── tests/                          # 50+ unit, integration, and fault-injection suites
│   ├── current/                        # Active project runtime workspaces (session.db, logs)
│   ├── data/                           # Central engine.db lifecycle database
│   ├── last_run/                       # Previous session snapshots
│   ├── history/                        # Historical session archive runs
│   └── ai_data/                        # AI analysis cache and provider configuration
│
└── README.md                           # Master repository documentation (this file)
```

---

## 3. First-Time Setup

### Prerequisites Checklist
Verify your environment before launching:
- **Python:** 3.11+ (Python 3.12 or 3.13 recommended) — `python --version`
- **Git:** 2.40+ accessible on `PATH` (required for Shadow Git) — `git --version`
- **Ollama (Optional):** Local LLM inference server (`ollama serve`) — `curl http://127.0.0.1:11434/api/tags`

### Installation Steps

```bash
# 1. Clone the repository and enter the runtime directory
git clone https://github.com/spdwivedi/spd_analysis_engine.git
cd spd_analysis_engine/spd_analysis_engine

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate the virtual environment
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Windows Command Prompt:
.venv\Scripts\activate.bat
# Linux / macOS bash:
source .venv/bin/activate

# 4. Install all dependencies
pip install -r requirements.txt

# 5. Initialize configuration files from examples
cp ai_data/models_config.example.json ai_data/models_config.json
cp ai_data/git_config.example.json    ai_data/git_config.json
```

> **Configuration Note:** Edit `ai_data/models_config.json` to configure local Ollama models (e.g. `qwen2.5-coder:7b`) or provide your Google Gemini API key if cloud analysis is desired.

---

## 4. CLI Operations Reference (`start.py`)

All commands are driven via [`spd_analysis_engine/start.py`](file:///D:/Project_Files/SPD_Analysis_Engine/spd_analysis_engine/start.py).

### Global Flags
These flags apply across all sub-commands:
- `-v`, `--verbose`: Enable detailed `INFO` logging.
- `--json`: Output command results as newline-delimited JSON for programmatic consumption.
- `--engine-root <DIR>`: Explicit path to the `spd_analysis_engine` root directory (defaults to script directory).

> [!IMPORTANT]
> **Headless Execution Clarification:**  
> There is **no `--headless` flag** on the root parser.
> - To run the **Web Control Portal without opening a browser window**, use `python start.py ui --no-browser`.
> - To run **isolated background worker processes without the web UI**, use `python start.py start`.

---

### Command: `ui` (Web Control Portal)

Starts the native HTTP server, binds the SSE real-time event bus, activates the authoritative supervisor, and launches the dashboard.

```bash
# Standard Launch (auto-opens browser to http://localhost:8765)
python start.py ui

# Custom Port
python start.py ui --port 9000

# Headless / Server Mode (does NOT open browser automatically)
python start.py ui --port 8765 --no-browser
```

---

### Command: `start` (Monitor Project Directory)

Spawns an isolated, detached OS worker process that continuously monitors a target directory, commits diffs to Shadow Git, and streams telemetry into SQLite.

```bash
python start.py start --project <NAME> --path <TARGET_DIRECTORY> [OPTIONS]
```

**Parameters & Flags:**
- `--project`, `-p <NAME>`: **(Required)** Unique project name or identifier.
- `--path <TARGET_PATH>`: **(Required)** Absolute path to the workspace directory to monitor.
- `--scaffold`: Automatically creates the target directory if it does not already exist.
- `--debounce <SECONDS>`: Sliding quiet window for burst sealing in seconds (default: `3.5s`, range: `0.5s` to `3600s`).
- `--track-reads` / `--no-track-reads`: Track IDE open-file handles via `psutil` (default: `True`).
- `--track-exec` / `--no-track-exec`: Track shell execution activity (default: `True`).
- `--shadow-git` / `--no-shadow-git`: Maintain isolated Shadow Git micro-versioning (default: `True`).
- `--git-init-primary`: Initialize primary Git repository in target path if missing (default: `False`).
- `--ide-profile <PROFILE>`: Select environment profile (`antigravity`, `cursor`, `windsurf`, `claude_code`, `custom`; default: `antigravity`).
- `--ide-custom-marker <MARKER>`: Custom process marker or executable name when using `custom` profile.

```bash
# Example: Monitor a project with custom debounce and primary Git initialization
python start.py start --project nova_pulse --path "D:/Projects/NovaPulse" --debounce 5.0 --git-init-primary
```

---

### Command: `status` (Worker Status Table)

Displays a formatted table of all running workers with real-time telemetry (PID, uptime, memory, CPU, heartbeat age, and session ID).

```bash
# Formatted status table of all active workers
python start.py status

# Inspect a specific project
python start.py status --project nova_pulse

# JSON output
python start.py status --json
```

---

### Command: `stop` (Graceful Worker Termination)

Cooperatively signals a worker process to stop, flushes pending in-flight bursts to Shadow Git, records the exit reason, and archives the session.

```bash
python start.py stop --project <NAME>
```

---

### Command: `list` (Project Registry)

Lists all registered projects across live memory and on-disk tiers (`current`, `last_run`, and `history`).

```bash
python start.py list
```

---

## 5. Web Dashboard (GUI) Guide

When running `python start.py ui`, open `http://localhost:8765` in your browser.

### Starting a Project
1. Click **"+ Add Project"** in the sidebar.
2. Enter the **Project Name** (e.g. `nova_pulse`).
3. Enter or browse the **Target Directory Path** to monitor.
4. Optionally configure the **Debounce Window** (default: `3.5s`) and **AI Profile** (`Google Antigravity`, `Cursor`, etc.).
5. Click **Start Monitoring**.

### Stopping a Project
Click the **Stop** button in the project view. The engine will:
1. Seal the current active burst.
2. Perform an atomic commit in Shadow Git.
3. Update session metadata and gracefully terminate the worker process.

### Resuming a Project
Click **Resume** on any inactive or stopped project card. The engine:
1. Verifies workspace directory availability.
2. Restores previously configured debounce windows, AI settings, and feature flags.
3. Spawns a fresh detached worker process resuming capture seamlessly.

### Real-Time Timeline & Diff Viewer
- **Live Stream:** As you save files in your IDE, bursts appear reactively in the timeline via Server-Sent Events (SSE).
- **On-Demand Diffs:** Click on any file within a burst to view unified syntax-highlighted diffs.
- **Non-Destructive Rollback:** Click **Rollback** on any historical burst to inspect a restoration dry-run and restore your workspace to that anchor point.

---

## 6. Runtime Data & Storage Tiers

Data is compartmentalized by lifecycle tier inside [`spd_analysis_engine/`](file:///D:/Project_Files/SPD_Analysis_Engine/spd_analysis_engine):

| Path | Purpose |
| :--- | :--- |
| **`data/engine.db`** | Central SQLite database tracking `projects`, `workers`, `operations`, and `ai_analyses`. |
| **`current/<slug>/`** | Active session workspace: `session.db`, WAL logs, `worker.status.json`, and `shadow_git/`. |
| **`current/<slug>/logs/worker.log`** | Structured worker log in JSON Lines; automatically rotates at 5 MB (retains 3 backups). |
| **`last_run/<slug>/`** | Snapshot of the immediately preceding session run. |
| **`history/<slug>/`** | Long-term archival runs with timestamped directories. |
| **`ai_data/models_config.json`** | Provider definitions for Ollama and Gemini models. |
| **`ai_data/cache/`** | Content-addressed AI cache (8-attribute composite keys) eliminating redundant LLM calls. |
| **`trash/`** | Soft-delete staging directory for reversible project removals. |

---

## 7. Troubleshooting

### Port Already in Use
**Error:** `OSError: [Errno 98] Address already in use` (Linux) or `WinError 10048` (Windows)  
**Cause:** A previously launched web server or test harness is still bound to port `8765`.  
**Fix:**
```powershell
# Windows: Find and terminate process on port 8765
netstat -ano | findstr :8765
taskkill /PID <PID> /F
```
```bash
# Linux / macOS: Kill process on port 8765
fuser -k 8765/tcp
```
Then re-launch: `python start.py ui --port 8765`.

---

### Zombie Worker Processes
If the engine is stopped ungracefully (e.g. forced terminal termination), background worker processes may linger. The engine's pre-flight checks and Reconciler daemon resolve orphans automatically, but you can force-clean manually:
```powershell
# Windows PowerShell: Terminate lingering project workers
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*project_worker*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
```bash
# Linux / macOS:
pkill -f "project_worker"
```

---

### Shadow Git Lock File
**Error:** `fatal: Unable to create '...shadow_git/.git/index.lock': File exists.`  
**Cause:** Host system reboot or crash occurred in the middle of a `git add` operation.  
**Fix:** Remove the stale index lock file:
```bash
# Remove stale lockfile for target project
rm current/<project_slug>/shadow_git/.git/index.lock
```

---

### Ollama Connection Refused
**Error:** `WinError 10061: No connection could be made because the target machine actively refused it`  
**Behavior:** This is **expected and non-fatal** when Ollama is not running. The engine falls back to heuristic analysis automatically without losing capture data.  
**Fix:** If local LLM synthesis is desired, launch the Ollama daemon:
```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

---

### SQLite WAL Database Corruption
If an unexpected storage hardware failure damages a project database:
```bash
python -c "
import sqlite3
conn = sqlite3.connect('current/<project_slug>/session.db')
print('Integrity Check:', conn.execute('PRAGMA integrity_check;').fetchall())
conn.execute('PRAGMA wal_checkpoint(FULL);')
conn.close()
print('WAL checkpoint complete')
"
```

---

### Process Reconciliation on Resume
When resuming a project after an unexpected host crash:
1. The Reconciler checks if the registered worker PID matches verified process attributes.
2. If the PID is dead, the prior session is cleanly marked `INTERRUPTED`.
3. A fresh worker process is spawned and opens a new session.
4. The dashboard displays a notification toast indicating recovery, preserving full audit history.

---

## 8. Security & Privacy Guarantees

1. **Local-First Default:** All telemetry, diffs, and database records remain strictly on local disk.
2. **Deterministic Sanitizer:** Secret scanner scrubs API keys, JWTs, AWS credentials, and SSH keys before external dispatch.
3. **Opt-In Cloud AI:** Cloud LLM analysis requires explicit per-project opt-in.
4. **Local API Hardening:** Enforces Host allowlists, Origin checks, body size limits, and ephemeral session tokens.
