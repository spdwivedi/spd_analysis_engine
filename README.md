# SPD Analysis Engine
### Local-First Passive IDE Telemetry & Micro-Versioning Engine

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)](https://python.org)
[![Unit Tests](https://img.shields.io/badge/Unit_Tests-100%2F100_✓-brightgreen)](./tests/)
[![Web UI](https://img.shields.io/badge/Web_UI-Zero_Dependency_ES6-orange?logo=javascript)](./web/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows_%7C_Linux-lightgrey?logo=linux)](.)
[![Local-First](https://img.shields.io/badge/Privacy-100%25_Local--First-green)](./notes/02_SECRETS_AND_CONFIG.md)

---

## What Is This?

The **SPD Analysis Engine** is a production-grade, zero-cloud developer telemetry and micro-versioning daemon that runs entirely on your local machine. It passively observes your IDE's filesystem activity, captures every atomic code burst (grouped file edits within a configurable debounce window), stores them in a hardened SQLite WAL database, commits them to an isolated Shadow Git repository, and presents a live reactive dashboard—all without touching any external server.

It was originally built and validated during the 5-phase creation of [NovaPulse](https://github.com/spdwivedi/nova_pulse), a production TypeScript simulation engine with 1,200+ entities, 160 unit tests, and a custom real-time physics tick loop—capturing every burst, diff, and EXEC event with **sub-0.15 ms telemetry overhead**.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                  start.py  (CLI entrypoint)                     │
│        python start.py ui --port 8765                           │
└────────────────────────┬────────────────────────────────────────┘
                         │ spawns
          ┌──────────────▼──────────────┐
          │   EngineWebServer           │  Native Python HTTPServer
          │   (scripts/server/)         │  SSE streaming, REST API
          └──────────────┬──────────────┘
                         │ manages
          ┌──────────────▼──────────────┐
          │   Supervisor                │  scripts/core/supervisor.py
          │   + Spawner / Reaper        │  Process lifecycle mgmt
          └──────┬───────────┬──────────┘
                 │           │
     ┌───────────▼──┐  ┌─────▼───────────────────┐
     │  Watcher FS  │  │  Project Worker (detached)│
     │  (inotify /  │  │  scripts/project_worker.py│
     │   ReadDir)   │  └─────┬────────────────────┘
     └───────────┬──┘        │
                 │  bursts   │  records
     ┌───────────▼───────────▼──────────────────────┐
     │           Event Bundler                       │
     │     Debounce Sliding Window (monotonic)       │
     └──────┬────────────────────┬───────────────────┘
            │                    │
  ┌─────────▼──────┐   ┌─────────▼──────────────────┐
  │  SessionDB     │   │  ShadowGit                  │
  │  SQLite WAL    │   │  scripts/git/shadow_repo.py │
  │  (ACID events, │   │  Isolated micro-commits,    │
  │  patches, AI)  │   │  unified diff deltas        │
  └────────────────┘   └────────────────────────────┘
            │
  ┌─────────▼──────────────────────────────────────┐
  │  AISynthesizer  (scripts/ai/)                  │
  │  Gemini API · Ollama · Heuristic fallback      │
  │  Hierarchical disk cache (project/session/burst│
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
| Database | SQLite in WAL mode | ACID guarantees, zero-server, concurrent reads during writes |
| Version control | Isolated Shadow Git | Non-intrusive to developer's own `.git`; captures every micro-save |
| Web UI | Vanilla ES6, no bundler | Zero runtime dependencies, sub-5 KB bundle, works offline |
| AI | Pluggable (Ollama / Gemini) | No vendor lock-in; degrades gracefully to heuristic mode |
| IPC | SSE (Server-Sent Events) | Unidirectional, HTTP/1.1 compatible, no WebSocket overhead |
| Concurrency | `threading.Lock` + detached `multiprocessing` workers | OS-level isolation per project, no GIL bottleneck |
| Privacy | All data stays on disk | Zero telemetry ever leaves the machine |

---

## Production Case Study: NovaPulse

The engine was developed and validated **co-operatively** with [NovaPulse](https://github.com/spdwivedi/nova_pulse), a TypeScript physics simulation testbed.

| Metric | Value |
|---|---|
| Total entities modeled | 1,200+ |
| Unit tests (NovaPulse) | 160 |
| Development phases tracked | 5 |
| Total bursts captured | 312 |
| Total EXEC events recorded | 847 |
| Avg. telemetry capture overhead | < 0.15 ms per burst |
| Shadow Git commits created | 312 micro-commits |
| AI burst analyses generated | 274 (via Gemini Flash) |
| Cache hit ratio (reloads) | 94.3% |
| Rollback operations tested | 17 (all reversions verified) |

**Phase Breakdown:**

- **Phase 1** — Core engine scaffolding (`supervisor.py`, `spawner.py`, SQLite schema)
- **Phase 2** — Filesystem watcher + debounce event bundler
- **Phase 3** — Shadow Git micro-versioning + unified diff delta extraction
- **Phase 4** — AI synthesis pipeline (Gemini + Ollama + heuristic fallback, hierarchical cache)
- **Phase 5** — Vanilla ES6 SSE dashboard, timeline, diff viewer, rollback tray

---

## System Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.12+ | `asyncio` task groups, `match` statements |
| Git | 2.40+ | Required for Shadow Git; must be on `PATH` |
| OS | Windows 10+, Ubuntu 22.04+, macOS 13+ | Windows tested with PowerShell 7+ |
| Ollama (optional) | Latest | For local LLM synthesis (`qwen2.5-coder:7b` recommended) |
| Gemini API Key (optional) | — | For cloud AI synthesis; configure in `ai_data/models_config.json` |

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

```bash
# Start with web dashboard
python start.py ui --port 8765

# Start headless (no browser)
python start.py --headless --port 8765

# Run the compliance audit (PowerShell)
powershell -ExecutionPolicy Bypass -File .\audit_engine.ps1
```

See [`notes/01_QUICKSTART_GUIDE.md`](./notes/01_QUICKSTART_GUIDE.md) for full CLI reference.

---

## API Endpoints (REST + SSE)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | Engine heartbeat, RAM, uptime |
| `GET` | `/api/projects` | List all monitored projects |
| `POST` | `/api/project/start` | Start monitoring a directory |
| `POST` | `/api/project/stop` | Stop monitoring, archive session |
| `POST` | `/api/project/<name>/resume` | Resume a previously stopped project |
| `GET` | `/api/project/<name>/events` | List all recorded burst events |
| `GET` | `/api/project/<name>/sessions` | List all sessions |
| `GET` | `/api/events/stream` | SSE stream (live telemetry push) |
| `POST` | `/api/project/<name>/analyze-event/<id>` | Trigger AI burst synthesis |
| `POST` | `/api/project/<name>/analyze-burst-overview` | AI architectural overview |
| `POST` | `/api/project/<name>/analyze-batch` | Background batch AI analysis |
| `POST` | `/api/project/<name>/rollback-burst` | Physical workspace rollback |
| `POST` | `/api/project/<name>/seal-burst` | Manually seal the current burst |
| `GET`  | `/api/project/<name>/export` | Export session as JSON |
| `POST` | `/api/git/push` | Push milestone commit to remote |

---

## Security & Privacy Guarantee

The SPD Analysis Engine is designed with **zero-trust cloud architecture**:

1. **100% Local-First** — No data is ever transmitted to any external server unless you explicitly configure GitHub push credentials.
2. **Token Masking** — All API keys stored in `models_config.json` are masked with `****` in API responses. The raw key is never serialized into logs or SSE events.
3. **Local AI Execution** — When using Ollama, the entire AI synthesis pipeline runs on your CPU/GPU. No data leaves the machine.
4. **Shadow Git Isolation** — The shadow `.git` directory lives at `current/<slug>/shadow_git/` — completely separate from your project's own `.git`.
5. **Sanitized Patch Filtering** — Files matching Salesforce DX patterns, `node_modules`, `__pycache__`, `.sf`, `.sfdx`, and compiled artifacts are automatically excluded from diff capture.
6. **No Telemetry Beaconing** — The engine never phones home. There is no analytics SDK, no crash reporter, no usage tracking.

---

## Testing

```bash
# Run full test suite (100 tests)
python -m unittest discover tests

# Run a specific module
python -m unittest tests.test_phase5

# Run with verbose output
python -m unittest discover tests -v
```

Expected: `Ran 100 tests in ~40s — OK`

> **Note:** Ollama connection errors (`WinError 10061`) are **expected and non-fatal** when no local Ollama instance is running. The engine gracefully falls back to heuristic mode.

---

## License

MIT — see [`LICENSE`](./LICENSE)

---

## Author & Co-Development Context

Built by **Surya Prakash Dwivedi** as a production systems engineering project, co-developed and validated against [NovaPulse](https://github.com/spdwivedi/nova_pulse).

