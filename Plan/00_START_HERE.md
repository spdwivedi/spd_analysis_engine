# SPD Analysis Engine: Specification-Driven Development (SDD) Control Plane

Welcome to the governance and architecture control plane for `SPD_Analysis_Engine`.

---

## 1. Project Layout & Boundaries

This repository enforces a strict boundary separation between governance and implementation:

```text
D:\Project_Files\SPD_Analysis_Engine\
├── Plan\                                # [GOVERNANCE PLANE] All architecture & specifications live here
│   ├── 00_START_HERE.md                 # System overview, boundaries, and invariants (this file)
│   ├── CURRENT_STATE.md                 # Active execution pointer and phase status
│   ├── DECISIONS.md                     # Locked Architectural Decision Records (ADRs)
│   ├── GLOBAL_ROADMAP.md                # 9-stage master roadmap (Stage 00 through Stage 08)
│   └── Reference\
│       └── History\
│           └── ARCHITECTURAL_AUDIT_2026.md  # Complete raw 2026 architectural audit
│
├── spd_analysis_engine\                 # [APPLICATION PLANE] Production code, tests, and runtime data
│   ├── scripts\                         # Core worker, server, and supervisor modules
│   ├── web\                             # Frontend UI assets (HTML, JS, CSS)
│   ├── tests\                           # Test suites
│   ├── current\                         # Runtime project workspaces (legacy tier)
│   ├── last_run\                        # Previous session data (legacy tier)
│   ├── history\                         # Historical session data (legacy tier)
│   └── ai_data\                         # AI models configuration and caches
│
└── engine.txt                           # Source text of the initial architectural audit
```

> [!IMPORTANT]
> **Boundary Guardrail:** All code modifications, refactorings, and file manipulations inside `spd_analysis_engine/` are gated by specification stages. When planning or auditing, the application directory is strictly read-only.

---

## 2. Core System Invariants

Every change implemented in this system must conform to these 30 locked invariants:

1. **Single Lifecycle Authority:** Exactly one lifecycle authority (`EngineController`) per engine instance. No secondary supervisors inside web servers or test harnesses.
2. **One Active Worker per Project:** Exactly one active worker process is permitted per monitored project.
3. **Read-Only GET Endpoints:** HTTP GET endpoints must never mutate state, trigger archives, run DDL, checkpoint WALs, or synthesize directories.
4. **Decoupled Lifecycle Authority:** Filesystem directories (`current/`, `last_run/`, `history/`) are storage views, never lifecycle decision-makers.
5. **Canonical Event Identity:** `event_id` is the globally unique, stable identity. Burst numbers are display-only artifacts.
6. **Display-Only Burst Numbers:** Burst numbers are never used to look up or resolve events.
7. **Universal UTC Time:** Persisted timestamps must use UTC ISO 8601 (`utc_now_iso()`). Local time offsets (e.g., IST) are formatted only at the presentation boundary.
8. **Multi-Attribute Worker Identity:** Worker process matching mandates verification of PID, process creation timestamp, executable path, exact UUID `worker_id` in command arguments, and canonical target directory.
9. **Reconciler Owns Process Loss:** Only the controller reconciler may transition a worker to `INTERRUPTED` or declare a process dead.
10. **Single Ordered Capture Consumer:** Each project has exactly one ordered `ChangeProcessor` consumer; bursts are never processed concurrently.
11. **Bounded Event Queues:** Watcher and event ingestion queues are strictly bounded to prevent out-of-memory errors under high event volume.
12. **Single SQLite Writer:** Each project's `session.db` has exactly one dedicated writer thread (`ProjectWriter`); all read connections are opened read-only.
13. **Event Durability Before Git:** Git failures or timeouts must never cause the loss of an already-persisted SQLite event.
14. **No Empty Commits:** No-op bursts never create Git commits; `--allow-empty` is strictly prohibited.
15. **Observable State Transitions:** Empty-file creations, deletions, and binary modifications are tracked as effective changes via file fingerprints (`before != after`).
16. **Non-Destructive Rollback:** Rollback restores workspace files to a target anchor while preserving forward history (`rolled_back`/`superseded` status) and emitting an auditable `ROLLBACK` event.
17. **Credential-Free Workers:** Worker processes never hold, inspect, or environment-inherit AI provider API keys.
18. **Durable AI Jobs:** AI analyses are scheduled as durable, lease-tracked jobs owned exclusively by the controller.
19. **Complete AI Cache Identity:** AI analysis cache keys must include target ID, execution profile, input hash, model name, provider, prompt version, sanitizer version, and context hash.
20. **Mandatory Secret Sanitization:** Sensitive credentials, tokens, and keys are scrubbed before payloads are dispatched to external AI providers.
21. **Bounded SSE Buffers:** Server-Sent Events (SSE) queues are bounded per client with drop/disconnect limits.
22. **Replayable SSE Streams:** SSE supports disconnection recovery via `Last-Event-ID`.
23. **Incremental Frontend Data Flow:** The UI loads summaries initially, listens for `burst_sealed` events, and fetches detailed diffs only on user expansion.
24. **Preserved Compatibility Contracts:** Public CLI arguments, API routes, and storage compatibility paths remain supported.
25. **Non-Destructive Migrations:** Historical session data is never deleted during schema migration.
26. **Idempotent Migrations:** Repeated schema migration runs are no-ops (`already_migrated`).
27. **Zero Destructive Cleanup on Reads:** No purge, rotate, or cleanup logic may be executed during query/read operations.
28. **No Substring Process Hunting:** Substring matches on process command lines or names are strictly forbidden.
29. **Hardened Local API Security:** Local API enforces Host allowlists, Origin validation on mutations, request size limits, and local session tokens. Wildcard CORS is removed.
30. **Zero Hidden Supervisors:** Neither `EngineWebServer` nor worker subprocesses may create standalone supervisor instances.

---

## 3. How to Navigate the Plan

- **[DECISIONS.md](file:///D:/Project_Files/SPD_Analysis_Engine/Plan/DECISIONS.md):** Consult for the locked architectural decisions (ADRs) that govern implementation choices.
- **[GLOBAL_ROADMAP.md](file:///D:/Project_Files/SPD_Analysis_Engine/Plan/GLOBAL_ROADMAP.md):** Track the 9 execution stages (Stages 00–08) and sub-phase milestones.
- **[CURRENT_STATE.md](file:///D:/Project_Files/SPD_Analysis_Engine/Plan/CURRENT_STATE.md):** View the currently active execution pointer and immediate work items.
- **[ARCHITECTURAL_AUDIT_2026.md](file:///D:/Project_Files/SPD_Analysis_Engine/Plan/Reference/History/ARCHITECTURAL_AUDIT_2026.md):** Access the original, unedited architectural audit text.
