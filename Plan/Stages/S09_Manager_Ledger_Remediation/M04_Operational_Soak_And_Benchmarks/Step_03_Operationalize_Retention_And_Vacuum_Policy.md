# Step 03 Operationalize Retention And Vacuum Policy
ID: S09-M04-Step03
Status: DONE

## 1. Objective & Scope
09.3: Establish auto-vacuum and artifact retention policy covering session.db, shadow_git objects, patches, and logs based on empirical 546 B/event cost.

- **Target Files:** `scripts/storage/schema_migration.py, scripts/storage/rotator.py`
- **Test Verification:** `bench/results.json, tests/test_storage_migration.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_storage_migration.py`.
- Syntax verification passes: `python -m py_compile scripts/storage/schema_migration.py`.

## 3. Execution Summary
- **Implementation:**
  - In `scripts/storage/schema_migration.py` (491 lines, strictly `<= 500 lines`): configured `PRAGMA auto_vacuum = INCREMENTAL;` automatically on fresh databases prior to schema initialization, and implemented `execute_incremental_vacuum(conn, pages=100)` to release freed database pages back to the filesystem.
  - In `scripts/storage/rotator.py` (349 lines, strictly `<= 500 lines`): implemented `get_project_artifact_sizes()` providing a disk usage breakdown across `session_db`, `wal`, `shadow_git`, `baseline`, `patches`, `logs`, and `history`, and `apply_artifact_retention_policy()` providing automated pruning of historical run archives beyond retention limits, loose object repack/pruning via `ShadowGit.run_maintenance(keep_refs)`, and `execute_incremental_vacuum()` triggers.
- **Test Verification:** Verified compilation via `python -m py_compile scripts/storage/schema_migration.py scripts/storage/rotator.py`. Added unit tests `test_incremental_auto_vacuum_and_vacuum_execution` and `test_artifact_size_inspection_and_retention_policy` in `tests/test_storage_migration.py`; all 13 tests passed cleanly.

