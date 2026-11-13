# Step 02 Wire Non Destructive Rollback
ID: S09-M01-P0-Step02
Status: DONE

## 1. Objective & Scope
B-2: Replace legacy destructive rollback path (git clean -fd, reset --hard, DELETE FROM events) with execute_non_destructive_rollback.

- **Target Files:** `scripts/server/routes_events.py, scripts/storage/rollback.py, scripts/git/shadow_rollback.py`
- **Test Verification:** `tests/test_rollback_and_autopush.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_rollback_and_autopush.py`.
- Syntax verification passes: `python -m py_compile scripts/server/routes_events.py`.

## 3. Execution Summary
- **Implementation:** Replaced `rollback_burst` call in `_handle_api_rollback_burst` (`scripts/server/routes_events.py`) with `execute_non_destructive_rollback()`. In `scripts/storage/rollback.py`, hardened `execute_non_destructive_rollback` to return `event_id` and `project`, handle null commits, and support non-destructive execution without deleting rows or calling `git clean -fd`. In `scripts/git/shadow_rollback.py`, updated `restore_files_non_destructive` to use NUL-delimited `--pathspec-from-file=- --pathspec-file-nul` to defeat Windows CLI buffer overflow, with robust error propagation. All 3 files strictly adhere to the `<= 500 lines` constraint (`routes_events.py`: 477 lines, `rollback.py`: 475 lines, `shadow_rollback.py`: 321 lines).
- **Test Verification:** Added `test_09_rollback_route_executes_non_destructive_and_no_git_clean` in `tests/test_rollback_and_autopush.py` verifying that the production route executes `execute_non_destructive_rollback`, never runs `git clean -fd`, and issues zero `DELETE FROM events`. All tests in `test_rollback_and_autopush.py`, `test_v9_crash_rollback_and_lap.py`, and `test_v10_physical_rollback_and_selective_trash.py` passed with 0 failures.

