# Step 04 Sanitize Sensitive File Diffs
ID: S09-M01-P0-Step04
Status: DONE

## 1. Objective & Scope
B-4: Enforce should_diff()/should_stage() checks in worker_bundle.py so plaintext secrets (.env, *.pem, *.key) never enter SQLite patches or baseline caches.

- **Target Files:** `scripts/core/worker_bundle.py, scripts/storage/ignore_policy.py, scripts/git/shadow_commit.py`
- **Test Verification:** `tests/test_provider_profiles_and_sanitizer.py`

## 2. Done-When Criteria
- Implementation applied strictly to target files with no regressions.
- Test command passes: `python -m unittest tests/test_provider_profiles_and_sanitizer.py`.
- Syntax verification passes: `python -m py_compile scripts/core/worker_bundle.py`.

## 3. Execution Summary
- **Implementation:**
  - `scripts/core/worker_bundle.py`: Wired `ignore_policy.should_diff(rel_path)` to replace diff text of sensitive files (.env, *.pem, *.key) with `[REDACTED SENSITIVE FILE]` (0 adds, 0 dels) and bypass updating baseline cache on disk and in memory. Excluded sensitive files from staging via `ignore_policy.should_stage(f)` when creating `ChangeSet`.
  - `scripts/git/shadow_commit.py`: Hardened `filter_gitignored_paths` to fail closed for sensitive files (`.env*`, `*.pem`, `*.key`, `*.pfx`, `id_rsa*`) so secrets are never staged in Shadow Git.
  - Kept `scripts/core/worker_bundle.py` (402 lines <= 500) and `scripts/git/shadow_commit.py` (496 lines <= 500) within micro-modularity limits.
- **Test Verification:**
  - Added `test_sensitive_file_diff_redacted_and_unversioned` to `tests/test_provider_profiles_and_sanitizer.py`.
  - Ran `python -m unittest tests.test_provider_profiles_and_sanitizer -v` (15/15 tests passed in 0.52s).
  - Verified `python -m py_compile scripts/core/worker_bundle.py` compiles cleanly.

