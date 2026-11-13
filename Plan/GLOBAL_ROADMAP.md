# Master Roadmap & Architectural Ledger

This roadmap documents the architectural milestones of the `SPD_Analysis_Engine`. The foundational refactoring (Stages 00 through 08) is fully completed and operational in the production codebase. Future maintenance and evolutionary enhancements are tracked in Stage 09.

---

## Historical Refactor (COMPLETED)

All 9 foundational refactoring stages (Stages 00–08) have been fully implemented, validated, and hardened in `spd_analysis_engine/`.

- [x] **Stage 00: Evidence and Freeze (COMPLETED)**
  - [x] **00.1 Code Bundle & Repository Inventory:** Complete source audit; `bundle_codebase.py` configured with strict secret and database exclusions.
  - [x] **00.2 Comprehensive Architecture Mapping:** Unified call site cataloging for supervisor, workers, DB writers, and SSE streams.
  - [x] **00.3 RED Regression Test Suite:** Verified test coverage across all 17 confirmed failure modes.
  - [x] **00.4 Non-Destructive Data Safety Snapshot:** Safety backup protocols for `current/`, `last_run/`, `history/`, and `ai_data/`.
  - [x] **00.5 Baseline Performance & Operational Metrics:** Latency, CPU, and process scan baselines recorded.
  - [x] **00.6 Deliverables Sign-off:** Baseline stabilization completed.

- [x] **Stage 01: Stop Destructive Behavior (COMPLETED)**
  - [x] **01.1 Eliminate Two-Supervisor Lifecycle Split (Phase 1A.1):** Web server takes injected authoritative controller; single supervisor in UI mode.
  - [x] **01.2 Make All Ordinary GET/Read Paths Strictly Read-Only (Phase 1A.2):** Zero auto-archiving or DDL on GETs; `auto_synthesize=False` enforced.
  - [x] **01.3 Canonical Slug & Project Identity (Phase 1A.3):** Unified `project_id`, `slug`, `display_name`, and `root_path` model.
  - [x] **01.4 Validated Configuration Contract & Debounce Defaults (Phase 1A.4):** `ProjectConfig` dataclass with `DEFAULT_DEBOUNCE_S = 3.5` and `max_burst_s = 60.0`.
  - [x] **01.5 Graceful Stop, UTC Timestamps, and Shutdown Ownership (Phase 1A.5):** Cooperative stop ladder; unified `utc_now_iso()` ISO 8601 timestamps.
  - [x] **01.6 Worker Identity & Exact Process Matching (Phase 1A.6):** UUID `worker_id` with 5-attribute process validation; elimination of substring scanning.
  - [x] **01.7 SQLite Concurrency & Writer RLock (Phase 1B.1):** Thread-safe database transactions and removal of duplicate `READ` telemetry events.
  - [x] **01.8 Patch Correctness & Git-Apply Verification (Phase 1B.2):** Standardized unified diff generation passing `git apply --check`.
  - [x] **01.9 EventBundler Timer Race Hardening (Phase 1B.3):** Lock reacquisition and max burst ceiling in event bundling.
  - [x] **01.10 Adaptive Process Telemetry (Phase 1B.4):** Dynamic scan intervals (0.5s–2.0s+) and process handle TTL caching.
  - [x] **01.11 Local API Security Hardening (Phase 1B.5):** Host allowlist, Origin validation, body size caps, and loopback session token authentication.
  - [x] **01.12 Package & Import Cleanup (Phase 1C):** Canonical package imports and compatibility facades in `scripts/facade.py`.

- [x] **Stage 02: Lifecycle Authority (COMPLETED)**
  - [x] **02.1 EngineController & engine.db Schema (Phase 2A):** Central `engine.db` registry with `EngineLock`, operation locks, and strict state machine.
  - [x] **02.2 Controller Reconciler & Authenticated Control Channel (Phase 2B):** Background Reconciler loop and loopback IPC control channel with idempotent protocol.

- [x] **Stage 03: Persistence Migration (COMPLETED)**
  - [x] **03.1 Permanent Project Storage Architecture (Phase 3A):** Permanent `data/projects/<slug>/` storage with virtualized legacy tier views and schema migrations.
  - [x] **03.2 Dedicated Single ProjectWriter (Phase 3B):** Dedicated background writer thread per project with FIFO write queuing, Future synchronization, and read-only query mode.

- [x] **Stage 04: Capture Pipeline (COMPLETED)**
  - [x] **04.1 Shared IgnorePolicy & Workspace Manifest (Phase 4A):** Layered `IgnorePolicy`, sub-millisecond `file_manifest`, and atomic `FsEvent` normalizer.
  - [x] **04.2 Condition-Variable BurstAssembler (Phase 4B):** Zero `threading.Timer` instances; dual-deadline wait loop, 20,000-event bounded queue, and fingerprint-based effective change detection.
  - [x] **04.3 Low-Overhead Adaptive Telemetry (Phase 4C):** Process telemetry decoupled from capture pipeline.

- [x] **Stage 05: Git & Change Engine (COMPLETED)**
  - [x] **05.1 ChangeSet & Scalable Shadow Git Operations (Phase 5A):** `ChangeSet` domain object, atomic Git burst commits (no `--allow-empty`), durable Git retry, and large-file truncation policy.
  - [x] **05.2 Non-Destructive Rollback & Safe Auto-Push (Phase 5B):** Auditable non-destructive rollback emitting `ROLLBACK` events; opt-in credential-isolated push jobs.

- [x] **Stage 06: API & Frontend (COMPLETED)**
  - [x] **06.1 Authoritative API Contract & Bounded SSE (Phase 6A):** Canonical contract registry in `api_contract.py`, lightweight summary endpoints, bounded SSE deques, and `Last-Event-ID` replay.
  - [x] **06.2 Incremental Frontend Data Flow (Phase 6B):** Incremental SPA data flow; on-demand diff retrieval and windowed scrolling for large bursts.

- [x] **Stage 07: AI Subsystem (COMPLETED)**
  - [x] **07.1 Controller-Owned AI Jobs & Durable Analysis Cache (Phase 7A):** Controller-exclusive AI execution, durable job queues in `engine.db`, composite 8-part cache keys, and progressive levels (L1–L4).
  - [x] **07.2 Provider Profiles, Sanitization, and Resource Governance (Phase 7B):** Execution profiles (`NORMAL`, `ECO`, `GPU_AI`, `OFFLINE`), pre-flight secret sanitizer, and per-project cloud consent gating.

- [x] **Stage 08: Cleanup & Hardening (COMPLETED)**
  - [x] **08.1 Deprecation, Observability, and Documentation (Phase 8A):** Obsolete module retirement, structured JSON Lines log rotation (`5 MB x 3`), and `/health` telemetry endpoint.
  - [x] **08.2 Deterministic Fault Injection & Benchmarks (Phase 8B):** `SPD_TEST_FAULTS` harness simulating crashes across 11 lifecycle stages; performance benchmark verification.
  - [x] **08.3 Final Architecture Audit & Drift Verification:** Verified compliance against all 30 core system invariants.

---

## Stage 09: Manager Review Ledger Remediation & Operational Soak (COMPLETED)
Remediated 41 findings from `MANAGEMENT_REVIEW_AND_FIXES_LEDGER.md`.

- [x] **M01: P0 Critical Blockers (B-1 through B-5)**
- [x] **M02: Live UX & Bytecode Remediation (Seal button, debounce clamp, live SSE)**
- [x] **M03: P1 High-Priority Hardening (Security, Windows stops, single-writer consistency)**
- [x] **M04: Operational Soak & Benchmarks (2-hour soak, retention auto-vacuum)**

## Stage 10: Future Scope & Maintenance

This stage tracks forward-looking enhancements, enterprise extensions, and continuous platform maintenance.

- [ ] **09.1 Distributed & Remote Worker Nodes**
  - [ ] Implement optional secure mTLS daemon transport for distributed workers monitoring remote file servers.
  - [ ] Add containerized worker deployment profiles (Docker / Podman execution environments).
  - [ ] Support headless worker orchestration via systemd unit files and Windows Service wrappers.

- [ ] **09.2 Advanced AI Provider Integrations & Local Embeddings**
  - [ ] Add native vector embedding indexing for semantic codebase change search using local models (e.g. `nomic-embed-text`).
  - [ ] Support custom OpenAI-compatible and Anthropic API endpoints in `provider_pool.py`.
  - [ ] Implement fine-grained AST-aware semantic diff summarization for major languages (Python, TypeScript, Rust, Go).

- [ ] **09.3 Scale & High-Frequency Ingestion Enhancements**
  - [ ] Benchmark and optimize memory footprint for workspaces exceeding 100,000 files.
  - [ ] Add native OS filesystem notification backends (Windows ReadDirectoryChangesW, Linux inotify, macOS FSEvents) as direct C-FFI alternatives to watchdog.
  - [ ] Implement parallel SHA-256 chunk hashing for multi-gigabyte project assets.

- [ ] **09.4 Webhooks & Integration Pipelines**
  - [ ] Implement configurable outgoing webhook dispatchers for `burst_sealed`, `milestone_reached`, and `rollback_executed`.
  - [ ] Build GitHub PR and GitLab MR automated audit comment integration via controller background jobs.
  - [ ] Add Slack and Discord notification integrations with configurable severity filtering.

- [ ] **09.5 Monorepo Multi-Targeting & Sub-Project Scoping**
  - [ ] Enable sparse-checkout and sub-directory targeting within massive monorepos.
  - [ ] Support multi-target dependency graphs across co-dependent project roots.
  - [ ] Provide unified cross-project timeline views in the Web Control Portal.
