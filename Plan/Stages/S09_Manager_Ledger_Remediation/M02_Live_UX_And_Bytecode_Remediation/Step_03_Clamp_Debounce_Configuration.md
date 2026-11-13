# Step 03 Clamp Debounce Configuration
ID: S09-M02-Step03
Status: DONE

## 1. Objective & Scope
Clamp maximum default debounce in project settings to 30.0s (default: 3.5s) to prevent accidental 600s/10-minute quiet period delays.

- **Target Files:** `scripts/core/project_config.py, web/js/views/modals/modal_workspace.js`
- **Test Verification:** `tests/test_project_config.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_project_config.py`.
- Syntax verification passes: `python -m py_compile scripts/core/project_config.py`.

## 3. Execution Summary
- **Implementation:** Added `MAX_STANDARD_DEBOUNCE_S = 60.0` and `extended_soak: bool = False` to `ProjectConfig`. In `validate()` and `update_from_dict()`, clamp `debounce_s > 60.0s` to `60.0s` with explicit warning unless `extended_soak=True`, ensuring `max_burst_s >= debounce_s`. In `web/js/views/modals/modal_base.js`, fixed `savedDebounce` fallback from `'600'` to `'3.5'`. In `modal_workspace.js`, added `clampWorkspaceDebounce()` and `DEFAULT_WORKSPACE_DEBOUNCE = 3.5`.
- **Test Verification:** Added `test_debounce_above_60s_clamped_unless_extended_soak` to `tests/test_project_config.py`. 10 tests passing cleanly. `scripts/core/project_config.py` verified at 440 lines (<= 500 lines).
