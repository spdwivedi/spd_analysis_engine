Step_06: Enforce Large File Policy
ID: S05-M01-P01-Step06

Status: DONE

Depends on: S05-M01-P01-Step05

1. Purpose
Enforce large file limits: truncate diff text > 1 MB, truncate patches > 2 MB, and record content hashes.

2. Scope
spd_analysis_engine/scripts/diff_calc.py, spd_analysis_engine/scripts/git/shadow_commit.py

3. Verification (Done when...)
Files exceeding limits are flagged with truncated=true and hashes recorded without RAM spikes.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/core/changeset.py, scripts/git/shadow_repo.py).
- **Test Verification:** Covered by passing test suite (tests/test_changeset_and_shadow_git.py).
