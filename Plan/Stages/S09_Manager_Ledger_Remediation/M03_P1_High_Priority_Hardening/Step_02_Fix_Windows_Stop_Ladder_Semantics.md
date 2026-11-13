# Step 02 Fix Windows Stop Ladder Semantics
ID: S09-M03-Step02
Status: DONE

## 1. Objective & Scope
Reorder stop ladder so sentinel/IPC STOP executes first; classify any TerminateProcess/SIGKILL escalation as INTERRUPTED (never clean STOPPED).

- **Target Files:** `scripts/controller/process.py, scripts/core/spawner.py, scripts/core/worker_helpers.py`
- **Test Verification:** `tests/test_safe_worker_shutdown.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_safe_worker_shutdown.py`.
- Syntax verification passes: `python -m py_compile scripts/controller/process.py`.

## 3. Execution Summary
- **Implementation:** In `scripts/controller/process.py`, reordered cooperative stop ladder to write `stop.cmd` file sentinel atomically first, suppressed instant `TerminateProcess` on Windows during cooperative stage, and re-verified PID create-time before forced escalation, returning `(clean_stop=False, "forced_kill")`. In `controller.py`, transitioned forced kills to `ProjectState.INTERRUPTED` with `end_reason="forced_kill"` and eliminated lock recursion conflict (F-2.2). In `worker_helpers.py`, made sentinel writes atomic with `.tmp` and `.replace()` and guarded terminate/kill calls with process lifetime checks.
- **Test Verification:** All 37 tests in `tests/test_safe_worker_shutdown.py` passing (36 passed, 1 skipped). All lifecycle tests in `test_authoritative_lifecycle.py` and `test_lifecycle_authority.py` passing. File line counts strictly <= 500 lines (`process.py`: 142 lines, `controller.py`: 401 lines, `worker_helpers.py`: 498 lines).
