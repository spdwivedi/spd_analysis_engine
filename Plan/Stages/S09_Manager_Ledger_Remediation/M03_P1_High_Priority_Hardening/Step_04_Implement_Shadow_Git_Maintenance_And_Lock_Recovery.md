# Step 04 Implement Shadow Git Maintenance And Lock Recovery
ID: S09-M03-Step04
Status: DONE

## 1. Objective & Scope
Implement stale index.lock recovery (age > 60s + no git PID) and scheduled loose object repacking/pruning with milestone anchor protection.

- **Target Files:** `scripts/git/shadow_commit.py, scripts/git/shadow_repo.py`
- **Test Verification:** `tests/test_changeset_and_shadow_git.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_changeset_and_shadow_git.py`.
- Syntax verification passes: `python -m py_compile scripts/git/shadow_commit.py`.

## 3. Execution Summary
- **Implementation:** Implemented `recover_stale_index_lock()` in `scripts/git/shadow_commit.py` and `scripts/git/shadow_repo.py` ensuring orphaned `index.lock` files older than 60s without an active git PID are unlinked automatically prior to staging. Implemented `run_maintenance(keep_refs)` in `ShadowGit` executing `repack -ad -q` and `prune --expire=30.days` with milestone and rollback refs protected natively and pinned under `refs/keep/`.
- **Test Verification:** Added `test_11_stale_index_lock_recovery` and `test_12_shadow_git_maintenance` to `tests/test_changeset_and_shadow_git.py`. All 15 tests in `test_changeset_and_shadow_git.py` passing. File line counts strictly <= 500 lines (`shadow_commit.py`: 492 lines, `shadow_repo.py`: 448 lines, `test_changeset_and_shadow_git.py`: 497 lines).
