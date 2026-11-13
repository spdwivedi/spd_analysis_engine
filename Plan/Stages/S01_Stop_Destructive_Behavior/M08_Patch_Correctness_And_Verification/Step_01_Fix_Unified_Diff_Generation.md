Step_01: Fix Unified Diff Generation
ID: S01-M08-P01-Step01

Status: DONE

Depends on: S01-M07-P01-Step02

1. Purpose
Ensure generated textual unified diffs include correct headers, line counts, and '\ No newline at end of file' markers.

2. Scope
spd_analysis_engine/scripts/diff_calc.py

3. Verification (Done when...)
git apply --check returns 0 for all generated text diffs across test fixtures.

## Execution Summary
- **Implementation:** Completed and verified in production codebase (spd_analysis_engine/scripts/diff_calc.py).
- **Test Verification:** Covered by passing test suite (tests/test_diff_calc_git_apply.py).
