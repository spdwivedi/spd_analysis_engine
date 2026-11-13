import os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
PLAN_DIR = ROOT / "Plan"
S09_DIR = PLAN_DIR / "Stages" / "S09_Manager_Ledger_Remediation"
S09_DIR.mkdir(parents=True, exist_ok=True)

MILESTONES = {
    "M01_P0_Critical_Blockers": [
        ("Step_01_Fix_ChangeProcessor_Deadlock.md", "S09-M01-P0-Step01",
         "B-1: Fix ChangeProcessor self-deadlock on 20k-file overflow bundles by replacing non-reentrant Lock with RLock and hoisting reconcile() outside the lock.",
         "scripts/core/change_processor.py", "tests/test_burst_assembler_and_processor.py"),
        ("Step_02_Wire_Non_Destructive_Rollback.md", "S09-M01-P0-Step02",
         "B-2: Replace legacy destructive rollback path (git clean -fd, reset --hard, DELETE FROM events) with execute_non_destructive_rollback.",
         "scripts/server/routes_events.py, scripts/storage/rollback.py, scripts/git/shadow_rollback.py", "tests/test_rollback_and_autopush.py"),
        ("Step_03_Fix_Rollback_IPC_Bridge.md", "S09-M01-P0-Step03",
         "B-3: Fix broken capture-pause bridge: replace missing send_control_command with send_worker_command, fix SEAL to call flush_now(), and add pause/resume to ProjectWatcher.",
         "scripts/storage/rollback.py, scripts/project_worker.py, scripts/watcher_fs.py", "tests/test_rollback_and_autopush.py"),
        ("Step_04_Sanitize_Sensitive_File_Diffs.md", "S09-M01-P0-Step04",
         "B-4: Enforce should_diff()/should_stage() checks in worker_bundle.py so plaintext secrets (.env, *.pem, *.key) never enter SQLite patches or baseline caches.",
         "scripts/core/worker_bundle.py, scripts/storage/ignore_policy.py, scripts/git/shadow_commit.py", "tests/test_provider_profiles_and_sanitizer.py"),
        ("Step_05_Wire_Modern_Controller_And_Single_Writer.md", "S09-M01-P0-Step05",
         "B-5: Wire LifecycleController, ReconcilerDaemon, and ProjectWriter into production entry points; unify root and data/ engine.db paths per ADR-020.",
         "start.py, scripts/server/server_daemon.py, scripts/core/supervisor.py, scripts/storage/writer.py", "tests/test_authoritative_lifecycle.py"),
    ],
    "M02_Live_UX_And_Bytecode_Remediation": [
        ("Step_01_Filter_Pycache_And_Bytecode.md", "S09-M02-Step01",
         "Drop __pycache__ and *.pyc bytecode files immediately across IgnorePolicy, ProjectWatcher, and DiffCalculator so they never enter bursts.",
         "scripts/storage/ignore_policy.py, scripts/watcher_fs.py, scripts/diff_calc.py", "tests/test_ignore_policy_manifest_watcher.py"),
        ("Step_02_Restore_Manual_Burst_Seal_Button.md", "S09-M02-Step02",
         "Restore interactive '⚡ Seal Burst Now' button in the dashboard toolbar and connect it to POST /api/project/<slug>/seal-burst.",
         "web/index.html, web/js/views/dashboard.js", "tests/test_frontend_incremental.py"),
        ("Step_03_Clamp_Debounce_Configuration.md", "S09-M02-Step03",
         "Clamp maximum default debounce in project settings to 30.0s (default: 3.5s) to prevent accidental 600s/10-minute quiet period delays.",
         "scripts/core/project_config.py, web/js/views/modals/modal_workspace.js", "tests/test_project_config.py"),
        ("Step_04_Stream_Live_Actions_Via_SSE.md", "S09-M02-Step04",
         "Broadcast lightweight live_action SSE events for file touches and terminal commands so the Live Activity Stream updates in real time prior to burst sealing.",
         "scripts/server/sse_stream.py, scripts/core/worker_bundle.py, web/js/stream.js", "tests/test_api_contract_events_sse.py"),
        ("Step_05_Display_Execution_Session_Badges.md", "S09-M02-Step05",
         "Ensure terminal execution cards in the UI timeline display their true parent Session #N tag rather than fallback placeholders.",
         "web/js/views/timeline.js", "tests/test_frontend_incremental.py"),
    ],
    "M03_P1_High_Priority_Hardening": [
        ("Step_01_Secure_Auth_Token_And_IPC_Headers.md", "S09-M03-Step01",
         "Remove unauthenticated token disclosure from GET /api/auth/token; enforce Host, Origin, and JSON Content-Type validation on the loopback control channel.",
         "scripts/server/api_contract.py, scripts/server/security.py, scripts/controller/control_channel.py", "tests/test_server_hardening.py"),
        ("Step_02_Fix_Windows_Stop_Ladder_Semantics.md", "S09-M03-Step02",
         "Reorder stop ladder so sentinel/IPC STOP executes first; classify any TerminateProcess/SIGKILL escalation as INTERRUPTED (never clean STOPPED).",
         "scripts/controller/process.py, scripts/core/spawner.py, scripts/core/worker_helpers.py", "tests/test_safe_worker_shutdown.py"),
        ("Step_03_Prevent_Raw_SQLite_Transaction_Bypasses.md", "S09-M03-Step03",
         "Route raw connection operations (git_ops, commit-attribution updates) through SessionDB._execute/transaction() to eliminate cross-thread interleaving.",
         "scripts/git/shadow_commit.py, scripts/core/worker_bundle.py, scripts/storage/db.py", "tests/test_session_db_concurrency.py"),
        ("Step_04_Implement_Shadow_Git_Maintenance_And_Lock_Recovery.md", "S09-M03-Step04",
         "Implement stale index.lock recovery (age > 60s + no git PID) and scheduled loose object repacking/pruning with milestone anchor protection.",
         "scripts/git/shadow_commit.py, scripts/git/shadow_repo.py", "tests/test_changeset_and_shadow_git.py"),
        ("Step_05_Wire_Cloud_AI_Consent_Toggle.md", "S09-M03-Step05",
         "Connect project configuration cloud_ai_enabled consent toggle so Gemini calls do not stall in perpetual ConsentRequiredError.",
         "scripts/ai/controller_executor.py, scripts/storage/project_storage.py, web/js/views/modals/modal_ai.js", "tests/test_provider_profiles_and_sanitizer.py"),
    ],
    "M04_Operational_Soak_And_Benchmarks": [
        ("Step_01_Instrument_Two_Hour_Developer_Soak.md", "S09-M04-Step01",
         "09.1: Build soak scraper script and execute 2-hour interactive developer soak against pilot workspace; assert 0 deadlocks and bounded RSS drift.",
         "bench/soak_runner.py", "tests/test_v22_stress_faults.py"),
        ("Step_02_Latency_And_CPU_Duty_Cycle_Benchmark.md", "S09-M04-Step02",
         "09.2: Profile end-to-end event-to-SSE latency and background process scanner CPU duty cycle across idle and storm workloads.",
         "bench/bench_engine.py", "bench/results.json"),
        ("Step_03_Operationalize_Retention_And_Vacuum_Policy.md", "S09-M04-Step03",
         "09.3: Establish auto-vacuum and artifact retention policy covering session.db, shadow_git objects, patches, and logs based on empirical 546 B/event cost.",
         "scripts/storage/schema_migration.py, scripts/storage/rotator.py", "bench/results.json"),
    ],
}

# 1. Create Milestone Directories and Step Files
for milestone_dir, steps in MILESTONES.items():
    m_path = S09_DIR / milestone_dir
    m_path.mkdir(parents=True, exist_ok=True)
    for filename, step_id, summary, target_files, test_target in steps:
        step_path = m_path / filename
        content = f"""# {filename.replace('.md', '').replace('_', ' ')}
ID: {step_id}
Status: PENDING

## 1. Objective & Scope
{summary}

- **Target Files:** `{target_files}`
- **Test Verification:** `{test_target}`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest {test_target}`.
- Syntax verification passes: `python -m py_compile {target_files.split(',')[0].strip()}`.

## 3. Execution Summary
- **Implementation:** PENDING
- **Test Verification:** PENDING
"""
        step_path.write_text(content, encoding="utf-8")

# 2. Update Plan/CURRENT_STATE.md
current_state_path = PLAN_DIR / "CURRENT_STATE.md"
current_state_text = f"""# Current State & Active Pointer

## Execution Status
- **Status:** `AMBER — CONDITIONAL (Gated by Manager Review Remediation)`
- **Test Matrix:** 544 Passed, 1 Skipped, 0 Failures (545 executed)
- **Active Pointer:** `Stage 09: Manager Review Ledger Remediation & Operational Soak`
- **Audit Ledger Date:** {datetime.now().strftime('%Y-%m-%d')}

---

## Active Phase Scope: Stage 09 — Manager Review Remediation

Following the senior architectural audit (`MANAGEMENT_REVIEW_AND_FIXES_LEDGER.md`), the engine status is classified as AMBER until the 5 critical P0 blockers, operational UI controls, and live streaming enhancements are remediated.

### 5 P0 Blockers to Resolve
1. **B-1 (Deadlock):** Re-entrant lock in `ChangeProcessor` on 20k-file overflow bundles (`scripts/core/change_processor.py:67,150–163,250`).
2. **B-2 (Rollback):** Production route currently executes destructive legacy `git clean -fd` path (`scripts/server/routes_events.py:425`).
3. **B-3 (Rollback IPC):** Broken PAUSE/SEAL/RESUME control bridge (`scripts/storage/rollback.py`).
4. **B-4 (Secret Leak):** Sensitive files (.env, *.pem) diffed into plaintext database patches (`scripts/core/worker_bundle.py`).
5. **B-5 (Architecture Drift):** Unify root vs `data/` `engine.db` paths and wire `LifecycleController`/`ProjectWriter` per ADR-020.

### Live Pilot Workstream (M02)
- Re-introduce the manual **"⚡ Seal Burst Now"** button in the dashboard.
- Clamp debounce configuration defaults in UI to prevent accidental 600s delays.
- Add strict bytecode filtering (`__pycache__`, `*.pyc`).
- Stream real-time file updates and terminal executions live via SSE before burst seals.
- Ensure terminal execution cards display their active Session ID badge.
"""
current_state_path.write_text(current_state_text, encoding="utf-8")

# 3. Append ADR-020 to Plan/DECISIONS.md
decisions_path = PLAN_DIR / "DECISIONS.md"
decisions_content = decisions_path.read_text(encoding="utf-8")
if "ADR-020" not in decisions_content:
    adr_entry = """| **ADR-020** | **Remediation of Manager Audit Ledger & Wiring of Modern Stack** | Wire the modern `LifecycleController`, `ProjectWriter`, and non-destructive rollback directly into production routes while removing legacy destructive fallbacks and un-checkpointed WAL directory copies. | **Locked** |\n"""
    decisions_path.write_text(decisions_content.strip() + "\n" + adr_entry, encoding="utf-8")

# 4. Reconcile Plan/GLOBAL_ROADMAP.md
roadmap_path = PLAN_DIR / "GLOBAL_ROADMAP.md"
roadmap_text = roadmap_path.read_text(encoding="utf-8")
if "Stage 09: Manager Review Ledger Remediation" not in roadmap_text:
    roadmap_text = roadmap_text.replace(
        "## Stage 09: Future Scope & Maintenance",
        "## Stage 09: Manager Review Ledger Remediation & Operational Soak (ACTIVE)\n"
        "Remediating 41 findings from `MANAGEMENT_REVIEW_AND_FIXES_LEDGER.md`.\n\n"
        "- [ ] **M01: P0 Critical Blockers (B-1 through B-5)**\n"
        "- [ ] **M02: Live UX & Bytecode Remediation (Seal button, debounce clamp, live SSE)**\n"
        "- [ ] **M03: P1 High-Priority Hardening (Security, Windows stops, single-writer consistency)**\n"
        "- [ ] **M04: Operational Soak & Benchmarks (2-hour soak, retention auto-vacuum)**\n\n"
        "## Stage 10: Future Scope & Maintenance"
    )
    roadmap_path.write_text(roadmap_text, encoding="utf-8")

print("Successfully synchronized Plan/ hierarchy with MANAGEMENT_REVIEW_AND_FIXES_LEDGER.md!")
