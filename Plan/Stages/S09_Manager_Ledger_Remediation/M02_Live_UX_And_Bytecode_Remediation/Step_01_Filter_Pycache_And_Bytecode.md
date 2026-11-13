# Step 01 Filter Pycache And Bytecode
ID: S09-M02-Step01
Status: DONE

## 1. Objective & Scope
Drop __pycache__ and *.pyc bytecode files immediately across IgnorePolicy, ProjectWatcher, and DiffCalculator so they never enter bursts.

- **Target Files:** `scripts/storage/ignore_policy.py, scripts/watcher_fs.py, scripts/diff_calc.py`
- **Test Verification:** `tests/test_ignore_policy_manifest_watcher.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_ignore_policy_manifest_watcher.py`.
- Syntax verification passes: `python -m py_compile scripts/storage/ignore_policy.py`.

## 3. Execution Summary
- **Implementation:** Added `.pyd` to `EDITOR_TEMP_EXTENSIONS`, `IGNORED_EXTENSIONS`, and `DEFAULT_IGNORED_EXTENSIONS`. Updated `IgnorePolicy.is_builtin_ignored()` to check all path segments against `BUILTIN_DIR_NAMES` (`__pycache__`, `.git`, `node_modules`, `.venv`), rejecting bytecode and caches at any depth. Added path filter checks in `watcher_fs.on_moved()`.
- **Test Verification:** Added `test_pycache_and_bytecode_rejection` in `tests/test_ignore_policy_manifest_watcher.py`. 11 tests passing cleanly. py_compile verified across all three files. Line counts: `ignore_policy.py` (337 lines), `watcher_fs.py` (279 lines), `diff_calc.py` (454 lines).
