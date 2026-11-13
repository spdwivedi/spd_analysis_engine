# SPD Analysis Engine
### Local-First Passive IDE Telemetry & Micro-Versioning Engine

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://python.org)
[![Unit Tests](https://img.shields.io/badge/Unit_Tests-100%2F100_✓-brightgreen)](./tests/)
[![Web UI](https://img.shields.io/badge/Web_UI-Zero_Dependency_ES6-orange?logo=javascript)](./web/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows_%7C_Linux-lightgrey?logo=linux)](.)
[![Local-First](https://img.shields.io/badge/Privacy-Local--First_by_Default-green)](./notes/02_SECRETS_AND_CONFIG.md)

---

## What Is This?

The **SPD Analysis Engine** is a production-grade, local-first developer telemetry and micro-versioning daemon that runs on your workstation. It passively observes your IDE's filesystem activity, captures every atomic code burst (grouped file edits within a condition-variable burst window), serializes them via an asynchronous single-writer queue into a hardened SQLite WAL database, commits them to an isolated Shadow Git repository, and presents a live reactive dashboard.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                  start.py  (CLI entrypoint)                     │
│        python start.py ui --port 8765                           │
└────────────────────────┬────────────────────────────────────────┘
                         │ spawns
          ┌──────────────▼──────────────┐
          │   EngineWebServer           │  Native Python ThreadingHTTPServer
          │   (scripts/server/)         │  SSE streaming (/api/events/stream), REST
          └──────────────┬──────────────┘
                         │ manages
          ┌──────────────▼──────────────┐
          │   LifecycleController       │  scripts/controller/
          │   + Authoritative Reconciler│  Process lifecycle, lease recovery
          └──────┬───────────┬──────────┘
                 │           │
     ┌───────────▼──┐  ┌─────▼───────────────────┐
     │  Watcher FS  │  │  Project Worker (detached)│
     │  (Watchdog + │  │  scripts/project_worker.py│
     │  IgnorePolicy│  └─────┬────────────────────┘
     └───────────┬──┘        │
                 │  events   │  records
     ┌───────────▼───────────▼──────────────────────┐
     │           BurstAssembler                      │
     │     Condition-Variable Sliding Window         │
     └──────┬────────────────────┬───────────────────┘
            │                    │
  ┌─────────▼──────┐   ┌─────────▼──────────────────┐
  │  ProjectWriter │   │  ShadowGit                  │
  │  Bounded Queue │   │  scripts/git/shadow_repo.py │
  │  SQLite WAL DB │   │  Isolated micro-commits,    │
  │  (ACID events) │   │  durable burst tracking     │
  └────────────────┘   └────────────────────────────┘
            │
  ┌─────────▼──────────────────────────────────────┐
  │  ControllerAIExecutor (scripts/ai/)            │
  │  Ollama · Gemini API · Heuristic fallback      │
  │  Execution Profiles (NORMAL, ECO, GPU_AI, etc) │
  │  Deterministic secret redaction                │
  └────────────────────────────────────────────────┘
            │
  ┌─────────▼──────────────────────────────────────┐
  │  Vanilla ES6 Dashboard  (web/)                 │
  │  SSE reactive stream · diff viewer · timeline  │
  └────────────────────────────────────────────────┘
```

### Key Design Decisions

| Concern | Choice | Rationale |
|---|---|---|
| Database | SQLite WAL via ProjectWriter | Single-writer queue prevents lock contention; concurrent reads without blocking |
| Version control | Isolated Shadow Git | Non-intrusive to developer's primary `.git`; durable burst commits with retry state |
| Web UI | Vanilla ES6, no bundler | Zero runtime dependencies, sub-5 KB bundle, windowed file lists for large bursts |
| AI Pipeline | Controller-hosted execution | Workers never hold API keys; pluggable Ollama / Gemini with durable leasing |
| IPC | SSE (Server-Sent Events) | Realtime push (`/api/events/stream`) with `Last-Event-ID` resumption and bounded ring buffers |
| Concurrency | Condition-variable debouncer | Zero busy-wait debouncing with backpressure drops and manifest reconciliation |
| Privacy | Local-first by default | All capture remains local; cloud AI is explicit opt-in with deterministic secret redaction |

---

## System Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | Tested on Python 3.11, 3.12, 3.13, 3.14 |
| Git | 2.40+ | Required for Shadow Git; must be on `PATH` |
| OS | Windows 10+, Ubuntu 22.04+, macOS 13+ | Windows tested with PowerShell 7+ |
| Ollama (optional) | Latest | For local LLM synthesis (`qwen2.5-coder:7b` recommended) |
| Gemini API Key (optional) | — | For cloud AI synthesis; explicit per-project opt-in required |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-org/spd_analysis_engine.git
cd spd_analysis_engine/spd_analysis_engine

# 2. Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Copy and configure secrets
cp ai_data/models_config.example.json ai_data/models_config.json
cp ai_data/git_config.example.json    ai_data/git_config.json
# Edit both files — see notes/02_SECRETS_AND_CONFIG.md

# 5. Launch the engine + dashboard
python start.py ui --port 8765
```

Open `http://localhost:8765` in your browser. The dashboard auto-connects via SSE.

---

## Directory Structure

```
spd_analysis_engine/
├── start.py                     # CLI entrypoint
├── requirements.txt             # Python dependencies
├── audit_engine.ps1             # Line-count compliance auditor
├── dashboard.html               # Standalone offline dashboard (mirror)
│
├── scripts/                     # All backend Python modules
│   ├── project_worker.py        # Per-project worker entrypoint
│   ├── orchestrator.py          # Multi-project orchestration
│   ├── watcher_fs.py            # Filesystem change watcher
│   ├── event_bundler.py         # Debounce burst grouper
│   ├── diff_calc.py             # Unified diff calculator
│   │
│   ├── core/                    # Supervisor & process lifecycle
│   ├── storage/                 # SQLite WAL engine + rotation
│   ├── git/                     # Shadow Git micro-versioning
│   ├── ai/                      # AI synthesis pipeline
│   ├── server/                  # HTTP server + route mixins
│   ├── shell/                   # Shell command interception
│   └── trash/                   # Soft-delete system
│
├── web/                         # Vanilla ES6 frontend
│   ├── index.html
│   ├── css/
│   └── js/
│
├── tests/                       # 100-test suite (unittest)
├── notes/                       # Developer runbooks
└── ai_data/                     # AI cache, configs (gitignored)
```

---

## Running the Engine

### CLI Commands & Flags (`start.py`)

```bash
# Launch Web Control Portal (default: http://127.0.0.1:8765)
python start.py ui [--port 8765] [--no-browser]

# Start monitoring a project workspace
python start.py start --project <name> --path <target_path> \
    [--scaffold] \
    [--debounce 3.5] \
    [--track-reads / --no-track-reads] \
    [--track-exec / --no-track-exec] \
    [--shadow-git / --no-shadow-git] \
    [--git-init-primary] \
    [--ide-profile {antigravity,cursor,windsurf,claude_code,custom}] \
    [--ide-custom-marker <marker>]

# Gracefully stop a running worker and archive session
python start.py stop --project <name>

# Inspect worker status, heartbeat freshness, PID, and memory
python start.py status [--project <name>]

# List all projects across storage tiers (current, last_run, history)
python start.py list

# Direct headless web server runner
python -m spd_analysis_engine.scripts.web_server [--host 127.0.0.1] [--port 8765]

# Global CLI options
#   --engine-root <dir>   Path to engine root directory
#   --json                Emit machine-readable JSON output
#   -v, --verbose         Enable verbose (INFO) logging
```

---

## API Endpoints (REST Contract & SSE)

All endpoints conform to the unified API contract table in [`scripts/server/api_contract.py`](./scripts/server/api_contract.py).

### System, Health & Observability
| Method | Path | Aliases | Description |
|---|---|---|---|
| `GET` | `/api/status` | `/status` | Supervisor status, active workers, total memory, AI rate limiter |
| `GET` | `/api/engine/health` | `/api/health`, `/health` | Subsystem diagnostics, queue depths, drop counters, writer lag, Git status, AI queue, scan durations, reconciler health |
| `GET` | `/api/contract` | — | Machine-readable catalog of all registered API contract endpoints |
| `GET` | `/api/auth/token` | — | API discovery token |
| `POST` | `/api/system/recovery/purge-zombies` | — | Authoritative reconciler cleanup of dead worker artifacts |

### Real-time SSE Stream
| Method | Path | Aliases | Description |
|---|---|---|---|
| `GET` | `/api/events/stream` | `/api/stream` | Real-time SSE event stream with `Last-Event-ID` replay, bounded per-client ring queues, and events: `burst_sealed`, `status_changed`, `job_progress`, `ai_completed`, `ai_failed` |

### Project Management
| Method | Path | Aliases | Description |
|---|---|---|---|
| `GET` | `/api/projects` | `/projects` | List all tracked projects across storage tiers (`current`, `last_run`, `history`) |
| `POST` | `/api/projects/start` | `/api/project/start` | Start monitoring a target project directory |
| `POST` | `/api/projects/stop` | `/api/project/stop` | Gracefully stop worker, seal burst, and archive session |
| `POST` | `/api/projects/{name}/resume` | `/api/project/{name}/resume` | Resume a previously stopped project |
| `POST` | `/api/projects/{name}/delete` | `/api/project/{name}/delete` | Move project to recoverable soft-delete trash |

### Bounded Events & On-Demand Diffs
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/events` | Bounded event summaries (`?since_id=&limit=&types=&session_id=`) without heavy diff bodies |
| `GET` | `/api/events/{id}` | Single event metadata + touched file list |
| `GET` | `/api/events/{id}/files/{path}/diff` | On-demand file unified diff delta |
| `GET` | `/api/projects/{name}/events` | Legacy event list endpoint with bounded results |
| `GET` | `/api/projects/{name}/sessions` | List all recorded sessions and burst metrics |
| `GET` | `/api/projects/{name}/logs` | Tail worker execution log |
| `GET` | `/api/projects/{name}/export` | Export session history as JSON |

### Rollback & Workspace
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/projects/{name}/rollback-burst/{id}` | Non-destructive dry-run / confirmed rollback with shadow git commit and restore plan |
| `POST` | `/api/projects/{name}/seal-burst` | Manually seal current pending burst |

### Durable AI Jobs & Analyses
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/ai/jobs/enqueue` | Enqueue durable AI analysis job |
| `GET` | `/api/ai/jobs/{id}` | Fetch durable AI job status |
| `POST` | `/api/ai/jobs/{id}/retry` | Retry failed AI job |
| `GET` | `/api/ai/analyses/{id}` | Fetch completed analysis result |
| `GET` | `/api/ai/analyses/target/{kind}/{id}` | Fetch all progressive L1..L4 analyses for target |
| `GET` / `POST` | `/api/ai/config` | Read or save AI provider configuration |
| `POST` | `/api/ai/test` | Test AI provider connection |

### Git & Remote Push
| Method | Path | Description |
|---|---|---|
| `GET` / `POST` | `/api/git/config` | Read or save Git configuration |
| `POST` | `/api/git/test` | Test Git connection |
| `POST` | `/api/git/create-repo` | Initialize remote Git repository |
| `POST` | `/api/projects/{name}/git-push` | Opt-in protected milestone push (refuses default branch unless configured; runs asynchronously) |

---

## Security & Privacy Guarantee

The SPD Analysis Engine is built with a **privacy-first, local-first default architecture**:

1. **Local-First by Default** — All event capture, telemetry, SQLite database storage, and Shadow Git micro-commits run 100% locally on your machine. When using local LLMs (Ollama) or heuristic analysis, no project content or telemetry ever leaves your machine.
2. **Explicit Per-Project Cloud AI Opt-In** — Cloud AI providers (such as Google Gemini) are strictly disabled by default. Cloud analysis requires explicit per-project consent via the `cloud_ai_enabled` setting. **When cloud AI is enabled, sanitized and redacted project code diffs and contextual snippets are transmitted to the user-configured cloud provider API.**
3. **Deterministic Secret Sanitization** — Prior to any external provider request, diffs and context pass through a deterministic redactor that strips AWS keys, GitHub tokens, Google API keys, OpenAI keys, Slack tokens, JWTs, PEM private keys, `password=`, `secret=`, `token=`, `api_key=`, and credential-bearing URLs. Sensitive files matching `IgnorePolicy` blocklists (such as `.env*`, `*.pem`, `*.key`, `id_rsa*`) are scrubbed and replaced with `[REDACTED FILE: <path>]`. The active sanitizer version (`v2.0`) is tracked in canonical AI cache keys.
4. **Credential Security** — API keys are stored in local configuration files and provided to upstream APIs strictly via HTTP request headers (e.g. `x-goog-api-key`), never in URL query strings. All keys are masked (`****`) in logs, telemetry, and API responses.
5. **Execution Profiles** — Profiles (`NORMAL`, `ECO`, `GPU_AI`, `OFFLINE`) dynamically configure concurrency, timeouts, context limits, Ollama GPU offload checks, and polling intensity. Under `OFFLINE`, external network calls are unconditionally blocked.
6. **Graceful Shutdown & Durable Recovery** — Workers drain condition-variable bursts and flush single-writer database queues before stopping. On unexpected termination, an `atexit` handler marks sessions as `INTERRUPTED`. The authoritative controller reconciler never prematurely archives a project solely because a PID died.
7. **No Telemetry Beaconing** — The engine contains no third-party usage analytics, crash-reporting SDKs, or phone-home beacons.

---

## Testing

```bash
# Run full test suite
python -m unittest discover tests

# Run specific test modules
python -m unittest tests.test_v12_health_and_diagnostics
python -m unittest tests.test_api_contract_events_sse
python -m unittest tests.test_read_idempotency
python -m unittest tests.test_refactor_baseline_regressions
```

---

## License

MIT — see [`LICENSE`](./LICENSE)

---

## Author & Co-Development Context

Built by **Surya Prakash Dwivedi** as a production systems engineering project, co-developed and validated against [NovaPulse](https://github.com/spdwivedi/nova_pulse).

